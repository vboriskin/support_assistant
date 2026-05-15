# 08. PII Masking

PII (Personally Identifiable Information) — самая ответственная часть системы. Если PII попадёт в эмбеддинги, в индекс или в LLM-запрос — это инцидент. Подход: **defense in depth** — несколько слоёв маскирования, аудит, тесты на «золотом» наборе.

## Что считаем PII

| Тип | Примеры | Токен замены |
|---|---|---|
| ФИО физлиц | «Иванов Иван Иванович», «Иванова И.И.» | `<PERSON>` |
| Телефоны | «+7 (495) 123-45-67», «8-800-...» | `<PHONE>` |
| Email | `user@bank.ru` | `<EMAIL>` |
| Паспорт | «4500 123456», серия+номер | `<PASSPORT>` |
| СНИЛС | «123-456-789 01» | `<SNILS>` |
| ИНН | «7707083893» (10 или 12 цифр) | `<INN>` |
| Номер карты | «4276 1234 5678 9012», 13–19 цифр Луна | `<CARD>` |
| Номер счёта | «40817810099910004312» (20 цифр) | `<ACCOUNT>` |
| Номер заявки | «APP-12345678», «ЗПК-...» — формат банка | `<APPLICATION_ID>` |
| Суммы денег | «150 000 руб.», «1 234 567.89 ₽» | `<AMOUNT>` |
| Даты рождения | «12.05.1985», в контексте «дата рождения:» | `<BIRTH_DATE>` |
| Адреса | «г. Москва, ул. ...» | `<ADDRESS>` |
| Логины | «ivan.ivanov», «i.ivanov@corp» — пользователь системы | `<USER_LOGIN>` |

## Что НЕ маскируем

- Названия модулей, продуктов, отделов: «Скоринг», «Документы», «Андеррайтинг».
- Технические идентификаторы, не привязанные к клиенту: ID логов, request_id, корреляционные ID.
- Названия ошибок: «ERROR_VALIDATION_FAILED», «ORA-12345».
- Общие термины: «выписка», «заявка», «кредит», «справка 2-НДФЛ».
- Роли пользователей системы: «оператор», «андеррайтер», «руководитель отдела».

## Архитектура

```
                    Текст на входе
                          │
                          ▼
                ┌──────────────────┐
                │  RegexMasker     │  ← быстрые предсказуемые форматы
                │  (телефоны,      │
                │   email, ИНН,    │
                │   паспорт, СНИЛС,│
                │   карты, счета,  │
                │   суммы, даты)   │
                └────────┬─────────┘
                         ▼
                ┌──────────────────┐
                │   NERMasker      │  ← Natasha для ФИО/адресов/орг
                │   (PII_NER_ENABLED)│
                └────────┬─────────┘
                         ▼
                ┌──────────────────┐
                │ ContextualHints  │  ← правила «дата рождения:» → BIRTH_DATE
                └────────┬─────────┘
                         ▼
                ┌──────────────────┐
                │   AuditLog       │  ← подсчёт замен по типам
                └────────┬─────────┘
                         ▼
                  Текст на выходе
```

## Реализация

### Типы PII

`core/pii/types.py`:

```python
from enum import Enum
from pydantic import BaseModel

class PIIType(str, Enum):
    PERSON = "PERSON"
    PHONE = "PHONE"
    EMAIL = "EMAIL"
    PASSPORT = "PASSPORT"
    SNILS = "SNILS"
    INN = "INN"
    CARD = "CARD"
    ACCOUNT = "ACCOUNT"
    APPLICATION_ID = "APPLICATION_ID"
    AMOUNT = "AMOUNT"
    BIRTH_DATE = "BIRTH_DATE"
    ADDRESS = "ADDRESS"
    USER_LOGIN = "USER_LOGIN"

class PIIMatch(BaseModel):
    """Одно найденное совпадение."""
    pii_type: PIIType
    original: str
    start: int
    end: int
    confidence: float = 1.0      # уверенность (для NER)
```

### RegexMasker

`core/pii/regex_masker.py`. Регулярки — самый надёжный слой для предсказуемых форматов.

```python
import re
from typing import Iterable
from .types import PIIType, PIIMatch


class RegexMasker:
    """Маскирование на регулярных выражениях."""

    # Порядок имеет значение: специфичные паттерны раньше общих
    # (карта раньше произвольной строки из 16 цифр и т.д.)
    PATTERNS: list[tuple[PIIType, re.Pattern]] = [
        # Email
        (PIIType.EMAIL, re.compile(
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
        )),
        # Телефоны: +7..., 8(...)..., 8-..., (495) 123-45-67
        (PIIType.PHONE, re.compile(
            r"(?:(?:\+7|8)[\s\-]?)?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}\b"
        )),
        # Карта (Луна не валидируем, просто 13-19 цифр сгруппированных)
        (PIIType.CARD, re.compile(
            r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"
        )),
        # Счёт — 20 цифр подряд
        (PIIType.ACCOUNT, re.compile(r"\b\d{20}\b")),
        # СНИЛС: 123-456-789 01
        (PIIType.SNILS, re.compile(r"\b\d{3}-\d{3}-\d{3}\s?\d{2}\b")),
        # ИНН: 10 или 12 цифр; ловим только в контексте «ИНН: ...»
        # (без контекста — слишком много ложных, поскольку 10/12 цифр — частый паттерн)
        (PIIType.INN, re.compile(
            r"(?i)(?<=инн[\s:]\s)\d{10,12}\b|(?<=ИНН[\s:])\s?\d{10,12}\b"
        )),
        # Паспорт: 4 цифры + пробел/нет + 6 цифр; ловим только в контексте
        (PIIType.PASSPORT, re.compile(
            r"(?i)(?:паспорт[^\d]{0,10})\d{4}\s?\d{6}\b"
        )),
        # Application ID — настраиваемый паттерн под формат банка
        # По умолчанию: APP-12345678 или 8+ цифр в контексте «заявка ...»
        (PIIType.APPLICATION_ID, re.compile(
            r"\b(?:APP|ЗПК|КЗ|ЗС)-?\d{4,}\b|(?<=заявк[аеуи]\s)№?\s?\d{6,}\b"
        )),
        # Суммы: «150 000 руб», «1 234.56 ₽», «100000 RUB»
        (PIIType.AMOUNT, re.compile(
            r"\b\d{1,3}(?:[\s,]\d{3})+(?:[.,]\d{1,2})?\s?(?:руб(?:лей|\.)?|₽|RUB|р\.)|"
            r"\b\d{4,}(?:[.,]\d{1,2})?\s?(?:руб(?:лей|\.)?|₽|RUB|р\.)"
        )),
        # Дата рождения: только в контексте «дата рождения», «д.р.», «г.р.»
        (PIIType.BIRTH_DATE, re.compile(
            r"(?i)(?:дата\s+рождения|д\.р\.|г\.р\.)[\s:]+\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}"
        )),
        # Логин: latin.dot @ corp domain
        (PIIType.USER_LOGIN, re.compile(
            r"\b[a-z]{1,20}\.[a-z]{1,20}@[a-z.]{3,30}\b"
        )),
    ]

    def find_all(self, text: str) -> list[PIIMatch]:
        """Найти все PII-совпадения. Не пересекающиеся (greedy left-to-right)."""
        matches: list[PIIMatch] = []
        for pii_type, pattern in self.PATTERNS:
            for m in pattern.finditer(text):
                # Проверка пересечений с уже найденными
                if any(not (m.end() <= ex.start or m.start >= ex.end) for ex in matches):
                    continue
                matches.append(PIIMatch(
                    pii_type=pii_type,
                    original=m.group(0),
                    start=m.start(),
                    end=m.end(),
                ))
        matches.sort(key=lambda x: x.start)
        return matches
```

### NERMasker (Natasha)

`core/pii/ner_masker.py`. Использует Natasha для русскоязычного NER.

```python
from typing import Iterable
import structlog
from .types import PIIType, PIIMatch

logger = structlog.get_logger(__name__)

try:
    from natasha import (
        Segmenter, MorphVocab, NewsEmbedding,
        NewsMorphTagger, NewsNERTagger, Doc,
    )
    _NATASHA_AVAILABLE = True
except ImportError:
    _NATASHA_AVAILABLE = False


class NERMasker:
    """NER-маскирование через Natasha (только если доступна)."""

    def __init__(self):
        if not _NATASHA_AVAILABLE:
            logger.warning("natasha.not_installed")
            self._available = False
            return
        self._segmenter = Segmenter()
        self._morph_vocab = MorphVocab()
        emb = NewsEmbedding()
        self._morph_tagger = NewsMorphTagger(emb)
        self._ner_tagger = NewsNERTagger(emb)
        self._available = True

    def find_all(self, text: str) -> list[PIIMatch]:
        if not self._available or not text:
            return []
        try:
            doc = Doc(text)
            doc.segment(self._segmenter)
            doc.tag_morph(self._morph_tagger)
            doc.tag_ner(self._ner_tagger)
        except Exception as e:
            logger.warning("ner.failed", error=str(e))
            return []
        matches: list[PIIMatch] = []
        for span in doc.spans:
            if span.type == "PER":
                matches.append(PIIMatch(
                    pii_type=PIIType.PERSON,
                    original=text[span.start:span.stop],
                    start=span.start,
                    end=span.stop,
                    confidence=0.85,
                ))
            elif span.type == "LOC":
                # LOC от Natasha бывает слишком широкий ("Россия", "Москва" — не PII)
                # Фильтруем по контексту: если рядом «улица», «дом», «г.» — это адрес
                ctx_start = max(0, span.start - 20)
                ctx = text[ctx_start:span.start].lower()
                if any(k in ctx for k in ["ул.", "улица", "дом", "г.", "город", "адрес"]):
                    matches.append(PIIMatch(
                        pii_type=PIIType.ADDRESS,
                        original=text[span.start:span.stop],
                        start=span.start,
                        end=span.stop,
                        confidence=0.7,
                    ))
        return matches
```

### Композиция

`core/pii/pipeline.py`:

```python
import re
from typing import NamedTuple
from .types import PIIType, PIIMatch
from .regex_masker import RegexMasker
from .ner_masker import NERMasker
from config.settings import Settings


class MaskingResult(NamedTuple):
    masked_text: str
    audit: dict[str, int]     # {pii_type: count}
    matches: list[PIIMatch]


class PIIMaskingPipeline:
    """Композиция RegexMasker + NERMasker."""

    def __init__(self, settings: Settings):
        self.regex = RegexMasker()
        self.ner = NERMasker() if settings.pii.ner_enabled else None
        self.strict = settings.pii.strict_mode

    def mask(self, text: str) -> MaskingResult:
        if not text:
            return MaskingResult(text, {}, [])

        all_matches: list[PIIMatch] = []
        all_matches.extend(self.regex.find_all(text))
        if self.ner:
            ner_matches = self.ner.find_all(text)
            # Убираем NER-матчи, пересекающиеся с regex (regex приоритетнее)
            for m in ner_matches:
                if not any(
                    not (m.end <= ex.start or m.start >= ex.end)
                    for ex in all_matches
                ):
                    all_matches.append(m)

        all_matches.sort(key=lambda m: m.start)

        # Применяем замены справа налево, чтобы не сбивать индексы
        masked = text
        audit: dict[str, int] = {}
        for m in reversed(all_matches):
            token = f"<{m.pii_type.value}>"
            masked = masked[:m.start] + token + masked[m.end:]
            audit[m.pii_type.value] = audit.get(m.pii_type.value, 0) + 1

        # Sanity-check: после маскирования не осталось «подозрительных» паттернов
        if self.strict:
            self._sanity_check(masked)

        return MaskingResult(masked_text=masked, audit=audit, matches=all_matches)

    def _sanity_check(self, masked: str) -> None:
        """Проверка, что в строгом режиме не осталось очевидной PII."""
        # Email
        if re.search(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", masked):
            raise PIIRemainsError("Email pattern still present after masking")
        # Телефон (упрощённый)
        if re.search(r"\+7\d{10}|\b8\d{10}\b", masked.replace(" ", "").replace("-", "")):
            raise PIIRemainsError("Phone-like sequence still present")
        # Карта
        if re.search(r"\b\d{16}\b", masked):
            raise PIIRemainsError("16-digit number sequence still present")


class PIIRemainsError(Exception):
    """Маскирование не удалось — PII осталась."""
```

## Маскирование структур (тикета целиком)

`core/pii/ticket_masking.py`. Для тикета нужно маскировать несколько полей и сохранить аудит.

```python
from copy import deepcopy
from core.models import Ticket
from .pipeline import PIIMaskingPipeline


def mask_ticket(ticket: Ticket, pipeline: PIIMaskingPipeline) -> tuple[Ticket, dict[str, int]]:
    """Возвращает новый Ticket с замаскированными полями и аудит-словарь."""
    masked = ticket.model_copy(deep=True)
    audit_total: dict[str, int] = {}

    def _merge_audit(a: dict, b: dict):
        for k, v in b.items():
            a[k] = a.get(k, 0) + v

    # Subject и description
    res = pipeline.mask(masked.subject)
    masked.subject = res.masked_text
    _merge_audit(audit_total, res.audit)

    res = pipeline.mask(masked.description)
    masked.description = res.masked_text
    _merge_audit(audit_total, res.audit)

    # Комментарии
    for c in masked.conversation:
        res = pipeline.mask(c.content)
        c.content = res.masked_text
        _merge_audit(audit_total, res.audit)

    return masked, audit_total
```

## «Золотой» набор для тестирования

`tests/fixtures/golden_pii.json` — набор пар (исходный текст, ожидаемый результат). Покрывает все типы PII.

```json
[
  {
    "name": "person_with_initials",
    "input": "Уважаемая Иванова Мария Петровна, ваш запрос получен.",
    "expected_masked": "Уважаемая <PERSON>, ваш запрос получен.",
    "expected_audit": {"PERSON": 1}
  },
  {
    "name": "phone_various_formats",
    "input": "Звонить +7 (495) 123-45-67 или 8-800-555-35-35",
    "expected_masked": "Звонить <PHONE> или <PHONE>",
    "expected_audit": {"PHONE": 2}
  },
  {
    "name": "email_in_text",
    "input": "Напишите на ivan.petrov@bank.ru до 18:00",
    "expected_masked": "Напишите на <EMAIL> до 18:00",
    "expected_audit": {"EMAIL": 1}
  },
  {
    "name": "amount",
    "input": "Сумма кредита 1 500 000 руб., ежемесячно 35 000 ₽",
    "expected_masked": "Сумма кредита <AMOUNT>, ежемесячно <AMOUNT>",
    "expected_audit": {"AMOUNT": 2}
  },
  {
    "name": "application_id",
    "input": "По заявке APP-87654321 требуется решение",
    "expected_masked": "По заявке <APPLICATION_ID> требуется решение",
    "expected_audit": {"APPLICATION_ID": 1}
  },
  {
    "name": "card_number",
    "input": "Списание с карты 4276 1234 5678 9012 на сумму",
    "expected_masked": "Списание с карты <CARD> на сумму",
    "expected_audit": {"CARD": 1}
  },
  {
    "name": "no_pii",
    "input": "Не работает кнопка «Сохранить» в модуле скоринга",
    "expected_masked": "Не работает кнопка «Сохранить» в модуле скоринга",
    "expected_audit": {}
  },
  {
    "name": "module_name_not_pii",
    "input": "Проблема в модуле «Андеррайтинг» при отправке выписки",
    "expected_masked": "Проблема в модуле «Андеррайтинг» при отправке выписки",
    "expected_audit": {}
  },
  {
    "name": "mixed",
    "input": "Иванов И.И. (тел. +7-495-123-45-67, ivan@bank.ru) подал заявку APP-12345 на 500 000 руб.",
    "expected_masked": "<PERSON> (тел. <PHONE>, <EMAIL>) подал заявку <APPLICATION_ID> на <AMOUNT>",
    "expected_audit": {"PERSON": 1, "PHONE": 1, "EMAIL": 1, "APPLICATION_ID": 1, "AMOUNT": 1}
  }
]
```

## Тест

`tests/unit/test_pii_masking.py`:

```python
import json
import pytest
from pathlib import Path
from core.pii.pipeline import PIIMaskingPipeline
from config.settings import get_settings


@pytest.fixture
def pipeline():
    settings = get_settings()
    return PIIMaskingPipeline(settings)


@pytest.fixture
def golden_cases():
    path = Path("tests/fixtures/golden_pii.json")
    return json.loads(path.read_text(encoding="utf-8"))


def test_golden_cases(pipeline, golden_cases):
    failures = []
    for case in golden_cases:
        result = pipeline.mask(case["input"])
        if result.masked_text != case["expected_masked"]:
            failures.append(
                f"{case['name']}: expected {case['expected_masked']!r}, "
                f"got {result.masked_text!r}"
            )
    assert not failures, "\n".join(failures)


def test_audit_counts(pipeline, golden_cases):
    for case in golden_cases:
        result = pipeline.mask(case["input"])
        for pii_type, count in case["expected_audit"].items():
            assert result.audit.get(pii_type, 0) == count, \
                f"{case['name']}: {pii_type} count mismatch"
```

## Метрика качества и непрерывный контроль

После каждого ингеста — сохраняем аудит-сводку:

```python
# В пайплайне:
masked_ticket, audit = mask_ticket(ticket, pipeline)
ticket_db.pii_audit_json = json.dumps(audit)
```

В админ-дашборде (см. `14-UI.md`) — графики:
- Среднее количество замен по типам PII за период.
- Тикеты, в которых маскирование сработало 0 раз (подозрительные — возможно, PII не нашлась).

## Расширение

Когда от Виктории придёт реальная CSV — там могут быть форматы, которых нет в дефолтных regex. Тогда:

1. Собираем 50–100 примеров из реальных тикетов.
2. Анализируем — какие PII-форматы там встречаются.
3. Добавляем новые regex или расширяем существующие.
4. Дополняем `golden_pii.json` новыми кейсами.
5. Прогоняем тесты — должны проходить.
6. Дальше всё, что попадает в индекс, маскируется уже улучшенным пайплайном.

## Не маскируем декларативно — но проверяем

Текст уже после маскирования может всё ещё содержать PII в нестандартном формате. На этот случай — `_sanity_check` в strict_mode + ручной аудит выборки.

В `services/ingest_orchestrator.py` после пайплайна:

```python
if settings.pii.audit_sample_rate > 0:
    if random.random() < settings.pii.audit_sample_rate:
        # Помечаем тикет как "под аудитом" — позже человек проверяет
        await mark_for_pii_audit(ticket.id)
```

В UI — отдельная страница «На аудит PII», где оператор видит маскированный текст и может подтвердить либо отметить, что что-то пропустили (это потом превращается в новый кейс в golden).
