"""CLI для запуска eval-набора.

    python -m scripts.run_evals --sample 5
    python -m scripts.run_evals --case-set typical

Отчёт сохраняется в ``evals/reports/<run_id>.json``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from sqlalchemy.ext.asyncio import async_sessionmaker

from adapters.embeddings.factory import create_embeddings_client
from adapters.llm.factory import create_llm_client
from adapters.text_search.factory import create_text_search
from adapters.vector_store.factory import create_vector_store
from config.logging import configure_logging, get_logger
from config.settings import get_settings
from db.engine import dispose_engine, get_engine, get_session_factory
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


async def _run(case_set: str, sample: int | None) -> int:
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    log = get_logger("scripts.run_evals")

    engine = get_engine(settings)
    sf: async_sessionmaker = get_session_factory(settings)

    llm = create_llm_client(settings)
    embeddings = create_embeddings_client(settings)
    vec = create_vector_store(settings, engine)
    fts = create_text_search(settings, engine)

    # Прогрев индексов — иначе первый запрос упрётся в ленивый CREATE.
    try:
        await vec.count()
        await fts.count()
    except Exception as e:
        log.warning("evals.warmup_failed", error=str(e))

    retrieval = RetrievalService(
        embeddings=embeddings,
        vector_store=vec,
        text_search=fts,
        settings=settings,
        reranker=create_reranker(llm, settings),
    )
    # Лог-репозиторий держим на одной сессии — пишет в ту же БД.
    async with sf() as session:
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
        )

        log.info("evals.start", case_set=case_set, sample=sample)
        report = await runner.run(case_set=case_set, sample_size=sample)

    try:
        await llm.aclose()
        await embeddings.aclose()
    finally:
        await dispose_engine()

    print(f"\nrun_id: {report.run_id}")
    print(f"cases:  {report.completed_cases}/{report.total_cases}")
    print(f"report: evals/reports/{report.run_id}.json")
    print("aggregate:")
    print(json.dumps(report.aggregate, indent=2, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run eval-suite")
    p.add_argument("--case-set", default="default", help="папка в evals/cases или 'default'")
    p.add_argument("--sample", type=int, default=None, help="прогнать только первые N кейсов")
    args = p.parse_args(argv)
    return asyncio.run(_run(args.case_set, args.sample))


if __name__ == "__main__":
    sys.exit(main())
