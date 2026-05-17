"""Интеграционные тесты ``SQLiteFTS5``."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from adapters.text_search.base import TextSearchRecord
from adapters.text_search.sqlite_fts import SQLiteFTS5
from config.settings import Settings

pytestmark = pytest.mark.integration


@pytest.fixture
async def fts(vec_engine: AsyncEngine) -> SQLiteFTS5:
    return SQLiteFTS5(Settings(), vec_engine)


async def test_upsert_and_search_top_k(fts: SQLiteFTS5) -> None:
    records = [
        TextSearchRecord(
            id="r1",
            target_type="kb_chunk",
            target_id="a1",
            title="Загрузка выписки",
            content="Инструкция как загрузить PDF выписку клиента в системе",
        ),
        TextSearchRecord(
            id="r2",
            target_type="ticket_summary",
            target_id="t1",
            title="Авторизация недоступна",
            content="Пользователь не может войти, ошибка SSO",
        ),
        TextSearchRecord(
            id="r3",
            target_type="kb_chunk",
            target_id="a2",
            title="Скоринг",
            content="Алгоритм расчёта скорингового балла",
        ),
    ]
    await fts.upsert(records)
    assert await fts.count() == 3

    hits = await fts.search("загрузка выписки", top_k=5)
    assert hits, "FTS5 не нашёл ничего для прямого запроса"
    assert hits[0].id == "r1"


async def test_filter_by_target_type(fts: SQLiteFTS5) -> None:
    await fts.upsert(
        [
            TextSearchRecord(
                id="r1",
                target_type="kb_chunk",
                target_id="a1",
                title="PDF загрузка",
                content="как загрузить",
            ),
            TextSearchRecord(
                id="r2",
                target_type="ticket_summary",
                target_id="t1",
                title="PDF не загружается",
                content="ошибка загрузки",
            ),
        ]
    )
    only_kb = await fts.search("PDF", top_k=10, target_types=["kb_chunk"])
    assert [h.target_type for h in only_kb] == ["kb_chunk"]


async def test_delete_by_target(fts: SQLiteFTS5) -> None:
    await fts.upsert(
        [
            TextSearchRecord(
                id="r1",
                target_type="ticket_summary",
                target_id="t1",
                title="одно",
                content="первое",
            ),
            TextSearchRecord(
                id="r2",
                target_type="ticket_summary",
                target_id="t2",
                title="другое",
                content="второе",
            ),
        ]
    )
    removed = await fts.delete_by_target("ticket_summary", ["t1"])
    assert removed == 1
    assert await fts.count() == 1


async def test_upsert_replaces_existing(fts: SQLiteFTS5) -> None:
    rec = TextSearchRecord(
        id="r1", target_type="kb_chunk", target_id="a1", title="старый", content="старая версия"
    )
    await fts.upsert([rec])
    rec2 = TextSearchRecord(
        id="r1", target_type="kb_chunk", target_id="a1", title="новый", content="новая версия"
    )
    await fts.upsert([rec2])
    assert await fts.count() == 1
    hits = await fts.search("новая", top_k=1)
    assert hits and hits[0].title == "новый"


async def test_sanitizes_dangerous_chars(fts: SQLiteFTS5) -> None:
    await fts.upsert(
        [
            TextSearchRecord(
                id="r1",
                target_type="kb_chunk",
                target_id="a1",
                title="вопрос",
                content="как загрузить выписку",
            )
        ]
    )
    # Кавычка/скобка в запросе не должны ломать FTS5 MATCH.
    hits = await fts.search('как (загрузить) "выписку"?', top_k=5)
    assert hits and hits[0].id == "r1"


async def test_empty_query_returns_empty(fts: SQLiteFTS5) -> None:
    assert await fts.search("", top_k=5) == []
    assert await fts.search("   ", top_k=5) == []
