"""POST /api/evals/run + GET /api/evals/runs[/{id}].

Runner работает в фоне; статус прогона мы определяем по файлу
``evals/reports/<run_id>.json`` (есть/нет, поле ``finished_at``,
``aggregate.errored_cases``). Сохранять состояние в БД не нужно: отчёт сам
является состоянием.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from api.dependencies import (
    _session_factory,
    embeddings_client as _emb_dep,
    get_user_id,
    llm_client as _llm_dep,
    settings_dep,
    text_search_client as _ts_dep,
    vector_store_client as _vs_dep,
)
from config.logging import get_logger
from config.settings import Settings
from db.repositories.conversations import ConversationsRepository
from db.repositories.llm_logs import LLMLogsRepository
from evals.judges.faithfulness import FaithfulnessJudge
from evals.judges.helpfulness import HelpfulnessJudge
from evals.runner import EvalRunner
from services.answer_formatter import AnswerFormatter
from services.assistant import AssistantService
from services.prompt_builder import PromptBuilder
from services.reranker import create_reranker
from services.retrieval import RetrievalService

logger = get_logger("api.evals")
router = APIRouter(prefix="/evals", tags=["evals"])

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_REPORTS_DIR = _PROJECT_ROOT / "evals" / "reports"
_CASES_DIR = _PROJECT_ROOT / "evals" / "cases"

_pending: set[str] = set()


class EvalRunRequest(BaseModel):
    case_set: str = Field(default="default")
    sample_size: int | None = Field(default=None, ge=1, le=1000)


class EvalCaseCreate(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)
    expected_sources: list[str] = Field(default_factory=list)
    must_mention: list[str] = Field(default_factory=list)
    must_not_mention: list[str] = Field(default_factory=list)
    expected_answer_summary: str = ""
    edge_case_type: str = Field(default="typical")
    category: str | None = None
    ticket_context: dict[str, Any] | None = None


def _summary_from_path(p: Path) -> dict[str, Any]:
    data = json.loads(p.read_text(encoding="utf-8"))
    aggregate = data.get("aggregate") or {}
    if data.get("finished_at") is None:
        status = "running"
    elif aggregate.get("errored_cases", 0) > 0 and not data.get("results"):
        status = "failed"
    else:
        status = "succeeded"
    return {
        "run_id": data.get("run_id"),
        "case_set": data.get("case_set"),
        "started_at": data.get("started_at"),
        "finished_at": data.get("finished_at"),
        "total_cases": data.get("total_cases"),
        "completed_cases": data.get("completed_cases"),
        "aggregate": aggregate,
        "status": status,
    }


async def _run_in_bg(run_id: str, body: EvalRunRequest, settings: Settings) -> None:
    _pending.add(run_id)
    try:
        llm = _llm_dep()
        embeddings = _emb_dep()
        vec = _vs_dep()
        fts = _ts_dep()

        retrieval = RetrievalService(
            embeddings=embeddings,
            vector_store=vec,
            text_search=fts,
            settings=settings,
            reranker=create_reranker(llm, settings),
        )
        factory = _session_factory()
        async with factory() as session:
            assistant = AssistantService(
                retrieval=retrieval,
                llm=llm,
                prompt_builder=PromptBuilder(settings),
                formatter=AnswerFormatter(),
                settings=settings,
                conversations_repo=ConversationsRepository(session),
                llm_logs_repo=LLMLogsRepository(session),
            )
            runner = EvalRunner(
                assistant=assistant,
                faithfulness_judge=FaithfulnessJudge(llm),
                helpfulness_judge=HelpfulnessJudge(llm),
                settings=settings,
                cases_dir=_CASES_DIR,
                reports_dir=_REPORTS_DIR,
            )
            await runner.run(
                case_set=body.case_set,
                sample_size=body.sample_size,
                run_id=run_id,
            )
    except Exception as e:  # noqa: BLE001
        logger.exception("evals.bg_failed", run_id=run_id, error=str(e))
        _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        (_REPORTS_DIR / f"{run_id}.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "case_set": body.case_set,
                    "started_at": None,
                    "finished_at": None,
                    "total_cases": 0,
                    "completed_cases": 0,
                    "aggregate": {"errored_cases": 1, "error": str(e)},
                    "results": [],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    finally:
        _pending.discard(run_id)


@router.post("/run")
async def run_evals(
    body: EvalRunRequest,
    background: BackgroundTasks,
    _user_id: Annotated[str, Depends(get_user_id)],
    settings: Annotated[Settings, Depends(settings_dep)],
) -> dict[str, str]:
    run_id = str(uuid.uuid4())
    background.add_task(_run_in_bg, run_id, body, settings)
    return {"run_id": run_id, "status": "started"}


@router.get("/runs")
async def list_runs() -> list[dict[str, Any]]:
    if not _REPORTS_DIR.exists():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(_REPORTS_DIR.glob("*.json"), reverse=True):
        try:
            out.append(_summary_from_path(p))
        except Exception:  # noqa: BLE001
            continue
    return out


@router.post("/cases")
async def create_eval_case(body: EvalCaseCreate) -> dict[str, Any]:
    """Сохраняет новый эталонный кейс в ``evals/cases/<category>/<auto-id>.json``.

    Используется UI-кнопкой «+ в eval-набор» под ответами ассистента —
    органический рост eval-набора из реальных вопросов операторов.
    """
    import json as _json

    category = (body.category or body.edge_case_type or "typical").strip() or "typical"
    valid = {"typical", "no_answer_in_kb", "ambiguous", "adversarial"}
    if body.edge_case_type not in valid:
        raise HTTPException(422, f"edge_case_type must be one of {sorted(valid)}")

    case_id = f"{category}_{uuid.uuid4().hex[:8]}"
    target_dir = _CASES_DIR / category
    target_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "case_id": case_id,
        "category": category,
        "query": body.query,
        "ticket_context": body.ticket_context,
        "expected_sources": body.expected_sources,
        "must_mention": body.must_mention,
        "must_not_mention": body.must_not_mention,
        "expected_answer_summary": body.expected_answer_summary,
        "edge_case_type": body.edge_case_type,
    }
    path = target_dir / f"{case_id}.json"
    path.write_text(_json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"case_id": case_id, "path": str(path)}


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    p = _REPORTS_DIR / f"{run_id}.json"
    if not p.exists():
        if run_id in _pending:
            return {"run_id": run_id, "status": "running"}
        raise HTTPException(404, detail="run not found")
    return json.loads(p.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------
# Diff между двумя прогонами
# ----------------------------------------------------------------------


def _load_report(run_id: str) -> dict[str, Any]:
    p = _REPORTS_DIR / f"{run_id}.json"
    if not p.exists():
        raise HTTPException(404, detail=f"run not found: {run_id}")
    return json.loads(p.read_text(encoding="utf-8"))


_METRICS = ("faithfulness", "helpfulness", "recall_at_5", "recall_at_10", "mrr")


@router.get("/diff")
async def diff_runs(a: str, b: str) -> dict[str, Any]:
    """Сравнение двух прогонов по кейсам.

    Запрос: <code>GET /api/evals/diff?a=runA&b=runB</code>. Возвращает per-case
    diff по метрикам (B − A) и агрегат: сколько кейсов улучшилось/просело/
    остались без изменений / пропали / новые.
    """
    rep_a = _load_report(a)
    rep_b = _load_report(b)
    res_a = {c["case_id"]: c for c in (rep_a.get("results") or [])}
    res_b = {c["case_id"]: c for c in (rep_b.get("results") or [])}

    all_ids = sorted(set(res_a) | set(res_b))
    cases: list[dict[str, Any]] = []
    counts = {"improved": 0, "regressed": 0, "same": 0, "only_a": 0, "only_b": 0}

    for cid in all_ids:
        ca = res_a.get(cid)
        cb = res_b.get(cid)
        if ca and not cb:
            counts["only_a"] += 1
            cases.append({"case_id": cid, "status": "only_in_a", "a": ca, "b": None, "deltas": {}})
            continue
        if cb and not ca:
            counts["only_b"] += 1
            cases.append({"case_id": cid, "status": "only_in_b", "a": None, "b": cb, "deltas": {}})
            continue
        deltas = {m: (cb.get(m, 0) or 0) - (ca.get(m, 0) or 0) for m in _METRICS}
        major = deltas.get("faithfulness", 0) + deltas.get("helpfulness", 0)
        # Кейс «просел», если падение по любой ключевой метрике > 0.1
        regressed_any = any(
            (deltas.get(m, 0) or 0) <= -0.1 for m in ("faithfulness", "helpfulness", "recall_at_5")
        )
        improved_any = any(
            (deltas.get(m, 0) or 0) >= 0.1 for m in ("faithfulness", "helpfulness", "recall_at_5")
        )
        if regressed_any and not improved_any:
            status = "regressed"
            counts["regressed"] += 1
        elif improved_any and not regressed_any:
            status = "improved"
            counts["improved"] += 1
        elif improved_any and regressed_any:
            status = "mixed"
            counts["improved"] += 1 if major > 0 else 0
            counts["regressed"] += 1 if major < 0 else 0
        else:
            status = "same"
            counts["same"] += 1
        cases.append(
            {
                "case_id": cid,
                "status": status,
                "deltas": deltas,
                "a": {
                    "answer_text": ca.get("answer_text", ""),
                    **{m: ca.get(m) for m in _METRICS},
                },
                "b": {
                    "answer_text": cb.get("answer_text", ""),
                    **{m: cb.get(m) for m in _METRICS},
                },
                "query": cb.get("query") or ca.get("query"),
            }
        )

    agg_a = rep_a.get("aggregate") or {}
    agg_b = rep_b.get("aggregate") or {}
    agg_delta = {
        f"{m}_avg": (agg_b.get(f"{m}_avg") or 0) - (agg_a.get(f"{m}_avg") or 0)
        for m in _METRICS
    }

    return {
        "a": {"run_id": a, "started_at": rep_a.get("started_at"), "aggregate": agg_a},
        "b": {"run_id": b, "started_at": rep_b.get("started_at"), "aggregate": agg_b},
        "summary": counts,
        "aggregate_delta": agg_delta,
        "cases": cases,
    }
