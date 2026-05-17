"""Eval runner.

Загружает кейсы из ``evals/cases/<set>/*.json``, прогоняет каждый через
``AssistantService``, считает retrieval-метрики и LLM-judges, агрегирует и
пишет JSON-отчёт в ``evals/reports/``. Параллелизм ограничен семафором
(`INGEST_LLM_CONCURRENCY` — те же лимиты, что у ингеста; eval бьёт по той же
LLM-квоте).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from time import time
from typing import Any

from pydantic import BaseModel, Field

from config.logging import get_logger
from config.settings import Settings
from core.models import (
    Answer,
    AssistantRequest,
    EvalCase,
    TicketContext,
)
from evals.judges.faithfulness import FaithfulnessJudge
from evals.judges.helpfulness import HelpfulnessJudge
from evals.metrics import compute_mrr, compute_recall_at_k
from services.assistant import AssistantService

logger = get_logger("evals.runner")

ProgressCallback = Callable[["RunReport"], Awaitable[None]] | None


def _now_iso() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"


class CaseResult(BaseModel):
    case_id: str
    category: str
    edge_case_type: str
    query: str
    answer_text: str
    retrieved_source_ids: list[str] = Field(default_factory=list)
    expected_source_ids: list[str] = Field(default_factory=list)
    recall_at_5: float = 0.0
    recall_at_10: float = 0.0
    mrr: float = 0.0
    faithfulness: float = 0.0
    faithfulness_explanation: str = ""
    helpfulness: float = 0.0
    helpfulness_explanation: str = ""
    must_mention_hits: int = 0
    must_mention_total: int = 0
    must_not_mention_violations: int = 0
    adversarial_passed: bool | None = None
    latency_ms: int = 0
    errors: list[str] = Field(default_factory=list)


class RunReport(BaseModel):
    run_id: str
    started_at: str
    finished_at: str | None = None
    case_set: str
    sample_size: int | None = None
    total_cases: int
    completed_cases: int = 0
    results: list[CaseResult] = Field(default_factory=list)
    aggregate: dict[str, Any] = Field(default_factory=dict)


class EvalRunner:
    def __init__(
        self,
        *,
        assistant: AssistantService,
        faithfulness_judge: FaithfulnessJudge,
        helpfulness_judge: HelpfulnessJudge,
        settings: Settings,
        cases_dir: Path | None = None,
        reports_dir: Path | None = None,
    ) -> None:
        self.assistant = assistant
        self.f_judge = faithfulness_judge
        self.h_judge = helpfulness_judge
        self.settings = settings
        self.cases_dir = cases_dir or Path("evals/cases")
        self.reports_dir = reports_dir or Path("evals/reports")
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self._sem = asyncio.Semaphore(max(1, settings.ingest.llm_concurrency))

    # ------------------------------------------------------------------
    # Загрузка кейсов
    # ------------------------------------------------------------------

    def load_cases(self, case_set: str = "default") -> list[EvalCase]:
        cases: list[EvalCase] = []
        if case_set == "default":
            roots = sorted(p for p in self.cases_dir.iterdir() if p.is_dir())
        else:
            roots = [self.cases_dir / case_set]
        for root in roots:
            if not root.exists():
                continue
            for p in sorted(root.glob("*.json")):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    cases.append(EvalCase(**data))
                except Exception as e:
                    logger.warning("evals.case_load_failed", path=str(p), error=str(e))
        return cases

    # ------------------------------------------------------------------
    # Прогон
    # ------------------------------------------------------------------

    async def run(
        self,
        *,
        case_set: str = "default",
        sample_size: int | None = None,
        run_id: str | None = None,
        progress_callback: ProgressCallback = None,
    ) -> RunReport:
        run_id = run_id or str(uuid.uuid4())
        cases = self.load_cases(case_set)
        if sample_size is not None:
            cases = cases[:sample_size]

        report = RunReport(
            run_id=run_id,
            started_at=_now_iso(),
            case_set=case_set,
            sample_size=sample_size,
            total_cases=len(cases),
        )

        async def _wrap(c: EvalCase) -> CaseResult:
            async with self._sem:
                return await self._run_case(c)

        # Используем as_completed, чтобы прогресс шёл по мере готовности.
        tasks = [asyncio.create_task(_wrap(c)) for c in cases]
        for fut in asyncio.as_completed(tasks):
            result = await fut
            report.results.append(result)
            report.completed_cases += 1
            if progress_callback:
                await progress_callback(report)

        # Возвращаем результаты в порядке case_id для воспроизводимости.
        report.results.sort(key=lambda r: r.case_id)
        report.aggregate = self._aggregate(report.results)
        report.finished_at = _now_iso()
        self._save_report(report)
        return report

    # ------------------------------------------------------------------

    async def _run_case(self, case: EvalCase) -> CaseResult:
        t0 = time()
        errors: list[str] = []
        try:
            req = AssistantRequest(
                query=case.query,
                conversation_id=None,
                ticket_context=(
                    TicketContext(**case.ticket_context)
                    if case.ticket_context
                    else None
                ),
            )
            answer: Answer = await self.assistant.answer(req)
        except Exception as e:
            errors.append(f"assistant_error: {e}")
            return CaseResult(
                case_id=case.case_id,
                category=case.category,
                edge_case_type=case.edge_case_type,
                query=case.query,
                answer_text="",
                expected_source_ids=list(case.expected_sources),
                latency_ms=int((time() - t0) * 1000),
                errors=errors,
            )

        retrieved_ids = [s.source_id for s in answer.used_sources]
        recall_5 = compute_recall_at_k(retrieved_ids, case.expected_sources, k=5)
        recall_10 = compute_recall_at_k(retrieved_ids, case.expected_sources, k=10)
        mrr = compute_mrr(retrieved_ids, case.expected_sources)

        text_lower = (answer.text or "").lower()
        must_hits = sum(1 for w in case.must_mention if w.lower() in text_lower)
        not_violations = sum(
            1 for w in case.must_not_mention if w.lower() in text_lower
        )

        try:
            f_score, f_expl = await self.f_judge.evaluate(
                answer=answer, sources=answer.used_sources
            )
        except Exception as e:
            f_score, f_expl = 0.0, f"judge_error: {e}"
            errors.append(str(e))

        no_answer_expected = case.edge_case_type == "no_answer_in_kb"
        try:
            h_score, h_expl = await self.h_judge.evaluate(
                query=case.query,
                answer=answer,
                expected_summary=case.expected_answer_summary,
                no_answer_expected=no_answer_expected,
            )
        except Exception as e:
            h_score, h_expl = 0.0, f"judge_error: {e}"
            errors.append(str(e))

        adversarial_passed: bool | None = None
        if case.edge_case_type == "adversarial":
            adversarial_passed = not_violations == 0

        return CaseResult(
            case_id=case.case_id,
            category=case.category,
            edge_case_type=case.edge_case_type,
            query=case.query,
            answer_text=answer.text,
            retrieved_source_ids=retrieved_ids,
            expected_source_ids=list(case.expected_sources),
            recall_at_5=recall_5,
            recall_at_10=recall_10,
            mrr=mrr,
            faithfulness=f_score,
            faithfulness_explanation=f_expl,
            helpfulness=h_score,
            helpfulness_explanation=h_expl,
            must_mention_hits=must_hits,
            must_mention_total=len(case.must_mention),
            must_not_mention_violations=not_violations,
            adversarial_passed=adversarial_passed,
            latency_ms=int((time() - t0) * 1000),
            errors=errors,
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _aggregate(results: list[CaseResult]) -> dict[str, Any]:
        if not results:
            return {}

        def avg(key: str) -> float:
            ok = [getattr(r, key) for r in results if not r.errors]
            return sum(ok) / len(ok) if ok else 0.0

        adv = [r for r in results if r.adversarial_passed is not None]
        adv_pass_rate = (
            sum(1 for r in adv if r.adversarial_passed) / len(adv) if adv else None
        )
        no_answer_cases = [r for r in results if r.edge_case_type == "no_answer_in_kb"]
        markers = ("не знаю", "нет информации")
        no_answer_pass = (
            sum(
                1
                for r in no_answer_cases
                if any(m in r.answer_text.lower() for m in markers)
            )
            / len(no_answer_cases)
            if no_answer_cases
            else None
        )

        return {
            "recall_at_5": avg("recall_at_5"),
            "recall_at_10": avg("recall_at_10"),
            "mrr": avg("mrr"),
            "faithfulness": avg("faithfulness"),
            "helpfulness": avg("helpfulness"),
            "must_mention_rate": (
                sum(r.must_mention_hits for r in results)
                / max(1, sum(r.must_mention_total for r in results))
            ),
            "must_not_mention_violations_total": sum(
                r.must_not_mention_violations for r in results
            ),
            "adversarial_pass_rate": adv_pass_rate,
            "no_answer_pass_rate": no_answer_pass,
            "avg_latency_ms": int(avg("latency_ms")),
            "errored_cases": sum(1 for r in results if r.errors),
        }

    def _save_report(self, report: RunReport) -> Path:
        path = self.reports_dir / f"{report.run_id}.json"
        path.write_text(
            report.model_dump_json(indent=2, exclude_none=True),
            encoding="utf-8",
        )
        return path
