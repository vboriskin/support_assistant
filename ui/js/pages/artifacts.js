/* Страница «Артефакты»: что нужно от команды поддержки, с примерами и шаблонами. */

import { showToast } from "../components/toast.js";

function _esc(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

/* ----------------------- Данные ----------------------- */

const ARTIFACTS = [
  // ───────── 1. Критично ─────────
  {
    id: "tickets-csv",
    group: "critical",
    title: "1. Исторические закрытые тикеты (корпус)",
    why: "Главный корпус ассистента. Без реальных тикетов индекс пустой — отвечать не на чем.",
    what: "Выгрузка из Service Manager: закрытые тикеты за последние 12 мес. С перепиской, авторами реплик, итоговым решением.",
    delivery: "Запросить у владельца SM выгрузку в CSV (UTF-8, разделитель ';' или ','). Минимум 500 строк, оптимально — 5–10 тысяч.",
    importable: true,
    importPath: "POST /api/ingest/csv → вкладка «Ингест»",
    format: "CSV",
    schema: {
      kind: "csv",
      headers: [
        "id", "external_id", "channel", "category", "module", "subject",
        "description", "author_role", "assignee", "status", "priority",
        "created_at", "closed_at", "tags",
        "comments_json",
      ],
      types: {
        channel: "email | messenger | chatbot | sm | phone | other",
        status: "open | in_progress | resolved | closed | cancelled",
        priority: "low | normal | high | critical",
        created_at: "ISO 8601 (2026-04-01T10:30:00) или YYYY-MM-DD",
        tags: "массив через '|', например: загрузка|документы",
        comments_json: "JSON-массив реплик (см. пример)",
      },
    },
    example: `external_id;channel;category;module;subject;description;author_role;status;created_at;closed_at;tags;comments_json
SM-12345;email;Документы;Документы;Не загружается скан паспорта;Андеррайтер: при загрузке PDF >5 МБ страница падает с 500.;underwriter;resolved;2026-04-01T10:30:00;2026-04-01T14:00:00;загрузка|pdf;[{"author_role":"support_l1","content":"Попробуйте уменьшить размер до 5 МБ.","created_at":"2026-04-01T11:00:00","is_internal":false},{"author_role":"underwriter","content":"Помогло, спасибо.","created_at":"2026-04-01T13:50:00","is_internal":false}]`,
    notes: [
      "PII (ФИО, паспорт, телефон, e-mail) маскируется автоматически на ингесте — не нужно чистить вручную.",
      "Если CSV большой — лучше zip-архивом, по 50–100 МБ кускам.",
      "Готовый образец: <code>data/sample_tickets.csv</code> (200 синтетических строк).",
    ],
  },

  {
    id: "modules-registry",
    group: "critical",
    title: "2. Реестр модулей и категорий",
    why: "Категоризатор и retrieval работают тем лучше, чем точнее знают, какие модули вообще существуют и как их называет пользователь.",
    what: "Список бизнес-модулей приложения с синонимами, владельцами и базовой срочностью. Используется как hint для LLM и для фильтра в UI «Тикеты».",
    delivery: "JSON-файл один раз от тимлида поддержки. Обновлять при появлении новых модулей.",
    importable: false,
    importPath: "Положить как <code>config/modules.json</code>, читается на старте.",
    format: "JSON",
    schema: {
      kind: "json-schema",
      content: {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "array",
        "items": {
          "type": "object",
          "required": ["module", "synonyms"],
          "properties": {
            "module": { "type": "string", "description": "Каноническое имя модуля" },
            "synonyms": { "type": "array", "items": { "type": "string" } },
            "owner_group": { "type": "string", "description": "Группа поддержки-владелец" },
            "urgency_baseline": { "enum": ["low", "normal", "high", "critical"] },
            "description": { "type": "string" }
          }
        }
      },
    },
    example: `[
  {
    "module": "Документы",
    "synonyms": ["загрузка документов", "сканы", "вложения", "файлы"],
    "owner_group": "support_l2_docs",
    "urgency_baseline": "normal",
    "description": "Подсистема загрузки и хранения документов клиента (PDF, сканы, фото)."
  },
  {
    "module": "Скоринг",
    "synonyms": ["скор", "решение", "автоматический ответ"],
    "owner_group": "risk_team",
    "urgency_baseline": "high",
    "description": "Автоматическая оценка кредитной заявки."
  }
]`,
  },

  {
    id: "assignee-groups",
    group: "critical",
    title: "3. Маппинг групп поддержки",
    why: "Категоризатор предлагает <code>suggested_assignee_group</code>. Без маппинга это поле пустое и эскалация бессмысленна.",
    what: "Кто отвечает за что: модуль → группа, тип обращения → группа.",
    delivery: "JSON один раз; обновлять при реорганизациях.",
    importable: false,
    importPath: "<code>config/assignee_groups.json</code>",
    format: "JSON",
    schema: {
      kind: "json-schema",
      content: {
        "type": "object",
        "properties": {
          "by_module": { "type": "object", "additionalProperties": { "type": "string" } },
          "by_type":   { "type": "object", "additionalProperties": { "type": "string" } },
          "default":   { "type": "string" }
        },
        "required": ["by_module", "default"]
      },
    },
    example: `{
  "by_module": {
    "Документы": "support_l2_docs",
    "Скоринг":   "risk_team",
    "Авторизация": "platform_team"
  },
  "by_type": {
    "incident":         "support_oncall",
    "access_request":   "iam_team",
    "feature_request":  "product_team"
  },
  "default": "support_l1"
}`,
  },

  {
    id: "glossary",
    group: "critical",
    title: "4. Глоссарий терминов и аббревиатур",
    why: "Поддержка пользуется внутренним жаргоном: «UW», «откат скоринга», «недоумок». LLM не знает корпоративных смыслов — добавляем в промпт.",
    what: "Словарь: термин → канон + синонимы + краткое определение.",
    delivery: "JSON. Можно собрать из общего вики/Confluence экспортом.",
    importable: false,
    importPath: "<code>core/prompts/glossary.json</code> — подмешивается в системный промпт.",
    format: "JSON",
    schema: {
      kind: "json-schema",
      content: {
        "type": "array",
        "items": {
          "type": "object",
          "required": ["term", "definition"],
          "properties": {
            "term": { "type": "string" },
            "synonyms": { "type": "array", "items": { "type": "string" } },
            "definition": { "type": "string" }
          }
        }
      },
    },
    example: `[
  {
    "term": "андеррайтер",
    "synonyms": ["UW", "андер", "андик"],
    "definition": "Сотрудник, оценивающий кредитный риск по заявке вручную."
  },
  {
    "term": "откат скоринга",
    "synonyms": ["переоценка", "rerun"],
    "definition": "Повторный запуск автоматического расчёта после изменения данных в заявке."
  }
]`,
  },

  {
    id: "pii-dictionary",
    group: "critical",
    title: "5. Словарь PII / секретов",
    why: "Базовый pii-pipeline закрывает ФИО, паспорта, телефоны, e-mail. Но в банке часто есть свои форматы: номера договоров, идентификаторы, которые тоже нужно маскировать.",
    what: "Список доп. regex-паттернов, которые маскируются на ингесте поверх дефолтных.",
    delivery: "JSON с regex + label. Согласовать с ИБ.",
    importable: false,
    importPath: "<code>config/pii_extra.json</code> + переменная <code>PII_EXTRA_PATTERNS_PATH</code>.",
    format: "JSON",
    schema: {
      kind: "json-schema",
      content: {
        "type": "array",
        "items": {
          "type": "object",
          "required": ["label", "pattern"],
          "properties": {
            "label": { "type": "string", "description": "Метка для замены, например APPLICATION_ID" },
            "pattern": { "type": "string", "description": "Python-совместимый regex" },
            "description": { "type": "string" }
          }
        }
      },
    },
    example: `[
  {
    "label": "APPLICATION_ID",
    "pattern": "\\\\bAPP-\\\\d{8,12}\\\\b",
    "description": "Идентификатор кредитной заявки (формат APP-12345678)."
  },
  {
    "label": "CONTRACT_NO",
    "pattern": "\\\\bКД-\\\\d{4}/\\\\d{4,6}\\\\b",
    "description": "Номер кредитного договора."
  }
]`,
  },

  // ───────── 2. База знаний ─────────
  {
    id: "kb-articles",
    group: "kb",
    title: "6. Статьи базы знаний (SOP, инструкции, FAQ)",
    why: "Это вторая нога RAG-поиска. На тикетах ассистент учится «как было», на KB — «как правильно сейчас».",
    what: "Markdown-файлы по операционным процедурам, частым обращениям, инструкциям. Один файл = одна статья.",
    delivery: "Zip-архив с *.md (рекурсивно). Заголовок берётся из первого <code>#</code>.",
    importable: true,
    importPath: "Вкладка «База знаний» → «Импорт zip / md». API: <code>POST /api/kb/bulk</code>.",
    format: "Markdown (.md в zip)",
    schema: {
      kind: "markdown",
      content: `---
# (опциональный front matter — не обязателен, можно начать сразу с заголовка)
---

# Заголовок статьи — становится title

## Симптом
Что видит пользователь.

## Причина
Что произошло на самом деле.

## Решение
1. Шаг.
2. Шаг.
3. Шаг.

## Если не помогло
Куда эскалировать.`,
    },
    example: `# Не загружается скан паспорта (PDF > 10 МБ)

## Симптом
При загрузке файла страница падает с ошибкой 500 или висит на 95%.

## Причина
Текущее ограничение на размер вложения — 10 МБ. На скан паспорта с высоким DPI это не всегда хватает.

## Решение
1. Открыть PDF в Acrobat (или онлайн-сервисом) и сжать до 5 МБ.
2. Загрузить заново.
3. Если повторяется — попросить клиента переснять страницу при освещении сверху, без бликов.

## Если не помогло
Эскалировать на support_l2_docs, приложить external_id тикета и пример файла.`,
    notes: [
      "Можно положить файлы по подпапкам (модули) — иерархия сохраняется в индексе через метаданные.",
      "Чанкование автоматическое (~1200 символов, перекрытие 100). Длинные документы не страшны.",
    ],
  },

  {
    id: "response-templates",
    group: "kb",
    title: "7. Шаблоны ответов клиенту (playbook)",
    why: "Сейчас ассистент сам собирает «Драфт ответа клиенту» в конце answer'а. С готовыми шаблонами от поддержки качество драфтов резко вырастет.",
    what: "Markdown с типовыми ответами под частые ситуации. Лучше — с пометкой когда применять.",
    delivery: "Те же *.md в KB, но с тегом <code>type: playbook</code> в заголовке или в названии файла (<code>playbook__не-загружается-документ.md</code>).",
    importable: true,
    importPath: "Через тот же массовый импорт KB.",
    format: "Markdown",
    schema: { kind: "markdown", content: "(произвольный markdown; рекомендуем фиксировать структуру: Когда применять / Шаблон / Что варьировать)" },
    example: `# Шаблон ответа: PDF не загружается из-за размера

## Когда применять
Клиент жалуется, что не может прикрепить документ; в тикете упомянуто 5xx или зависание загрузки.

## Шаблон
Здравствуйте!

Спасибо за обращение. Сейчас в кабинете действует ограничение в 10 МБ на один документ.
Чтобы загрузить скан паспорта, попробуйте, пожалуйста:

1. Открыть PDF и сжать его (например, в Acrobat → «Уменьшить размер файла»).
2. Снизить разрешение скана до 200 DPI — этого достаточно для распознавания.

Если после этого загрузка всё ещё не проходит — пришлите файл нам в ответ на это письмо, мы загрузим со своей стороны.

## Что варьировать
- Если канал — мессенджер: убрать «Спасибо за обращение» и формальное приветствие.
- Если повторное обращение — заменить первый абзац на «Возвращаемся к вашему вопросу».`,
  },

  {
    id: "known-issues",
    group: "kb",
    title: "8. Список известных багов / known issues",
    why: "Чтобы ассистент не отправлял клиента «попробуйте ещё раз», когда на самом деле баг известен и есть workaround.",
    what: "Активные проблемы: симптом, обходной путь, jira-ticket, статус.",
    delivery: "Можно как KB-статьи (с тегом <code>known_issue</code>) или как отдельный JSON.",
    importable: true,
    importPath: "Импорт как KB (через bulk) с тегом <code>known_issue</code>. Альтернатива: JSON в отдельный endpoint (roadmap).",
    format: "JSON",
    schema: {
      kind: "json-schema",
      content: {
        "type": "array",
        "items": {
          "type": "object",
          "required": ["title", "symptom", "status"],
          "properties": {
            "title": { "type": "string" },
            "symptom": { "type": "string" },
            "workaround": { "type": "string" },
            "module": { "type": "string" },
            "status": { "enum": ["open", "workaround", "fixed"] },
            "jira": { "type": "string" },
            "discovered_at": { "type": "string", "format": "date" },
            "expected_fix": { "type": "string", "format": "date" }
          }
        }
      },
    },
    example: `[
  {
    "title": "Скан больше 10 МБ падает с 500",
    "symptom": "При загрузке PDF >10 МБ страница падает.",
    "workaround": "Сжать до 5 МБ либо разбить на 2 файла.",
    "module": "Документы",
    "status": "workaround",
    "jira": "DOCS-4421",
    "discovered_at": "2026-03-12",
    "expected_fix": "2026-06-01"
  }
]`,
  },

  // ───────── 3. Качество и оценка ─────────
  {
    id: "eval-cases",
    group: "quality",
    title: "9. Эталонные пары «вопрос — ответ» (eval-кейсы)",
    why: "Без эталонов мы не отличим регрессию от шума. Это основной механизм контроля качества при изменении модели/промптов/индекса.",
    what: "Реальные вопросы операторов с ожидаемыми источниками и проверками на упоминания.",
    delivery: "Можно одной кнопкой «+ В eval-набор» под ответом ассистента. Можно файлами JSON.",
    importable: true,
    importPath: "Кнопка под ответом ассистента (диалог с полями). API: <code>POST /api/evals/cases</code>.",
    format: "JSON (по одному файлу на кейс) либо JSON-массив",
    schema: {
      kind: "json-schema",
      content: {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "required": ["query", "edge_case_type"],
        "properties": {
          "query":   { "type": "string", "description": "Что спрашиваем у ассистента" },
          "edge_case_type": { "enum": ["typical", "no_answer_in_kb", "ambiguous", "adversarial"] },
          "category": { "type": "string", "description": "Бизнес-категория, для группировки" },
          "expected_sources": {
            "type": "array",
            "items": { "type": "string" },
            "description": "Идентификаторы источников, которые retrieval должен поднять (kb_chunk:..., ticket_summary:...)"
          },
          "must_mention":     { "type": "array", "items": { "type": "string" } },
          "must_not_mention": { "type": "array", "items": { "type": "string" } },
          "expected_answer_summary": { "type": "string" },
          "ticket_context": {
            "type": "object",
            "properties": {
              "subject": { "type": "string" },
              "description": { "type": "string" },
              "module": { "type": "string" }
            }
          }
        }
      },
    },
    example: `{
  "case_id": "typical_pdf_size_001",
  "category": "Документы",
  "edge_case_type": "typical",
  "query": "Клиент не может загрузить скан паспорта, файл 12 МБ",
  "expected_sources": ["kb_chunk:doc-loading#2", "ticket_summary:SM-12345"],
  "must_mention": ["10 МБ", "сжать"],
  "must_not_mention": ["передать в IT"],
  "expected_answer_summary": "Объяснить ограничение 10 МБ, предложить сжать PDF, если не помогает — эскалация на L2_docs.",
  "ticket_context": {
    "subject": "Не грузится паспорт",
    "description": "Андеррайтер не может прикрепить файл, размер 12 МБ.",
    "module": "Документы"
  }
}`,
    notes: [
      "Соберите 30–50 кейсов разной природы (typical / no_answer / ambiguous / adversarial) — этого хватит для baseline.",
      "Adversarial — попытки 'обмануть' ассистента: jailbreak, требование выдать PII клиента.",
    ],
  },

  {
    id: "historical-quality",
    group: "quality",
    title: "10. Исторические оценки качества ответов",
    why: "Если поддержка уже оценивала свои собственные ответы (NPS, CSAT, RTH) — мы можем сопоставить с тикетами и понять «золотые» ответы.",
    what: "CSV: ticket_id → оценка клиента/QA, комментарий.",
    delivery: "CSV выгрузкой из QA-системы (если есть).",
    importable: false,
    importPath: "Roadmap: scripts/import_quality_scores.py — пока разово через CLI.",
    format: "CSV",
    schema: {
      kind: "csv",
      headers: ["ticket_external_id", "score", "reviewer", "category", "comment"],
      types: {
        score: "целое от 1 до 5 либо -1/0/1",
        reviewer: "client | qa | l2",
      },
    },
    example: `ticket_external_id;score;reviewer;category;comment
SM-12345;5;client;Документы;Помогло с первого ответа.
SM-12480;2;qa;Скоринг;Не дал понятного объяснения, перевёл на L2.`,
  },

  // ───────── 4. Аналитика и приоритизация ─────────
  {
    id: "top-categories",
    group: "analytics",
    title: "11. Топ-категорий обращений за 3–6 месяцев",
    why: "Чтобы знать, куда копать в первую очередь: на каких темах ассистент даст самый большой эффект.",
    what: "Категория → количество обращений → среднее время закрытия → пик нагрузки.",
    delivery: "CSV из BI/Service Manager.",
    importable: false,
    importPath: "Пока — справочно, для product-решений. Roadmap: импорт + dashboard.",
    format: "CSV",
    schema: {
      kind: "csv",
      headers: ["category", "module", "count_3m", "avg_close_minutes", "peak_day"],
      types: {
        peak_day: "YYYY-MM-DD",
      },
    },
    example: `category;module;count_3m;avg_close_minutes;peak_day
Загрузка документов;Документы;842;47;2026-03-18
Откат скоринга;Скоринг;391;112;2026-04-02
Сброс пароля;Авторизация;612;12;2026-02-25`,
  },

  {
    id: "sla-matrix",
    group: "analytics",
    title: "12. SLA-матрица",
    why: "Категоризатор может выдавать <code>urgency</code> на основе SLA, а не «как чувствую».",
    what: "module × urgency → срок реакции в минутах.",
    delivery: "JSON, согласовать с продакт-овнером.",
    importable: false,
    importPath: "<code>config/sla.json</code>",
    format: "JSON",
    schema: {
      kind: "json-schema",
      content: {
        "type": "array",
        "items": {
          "type": "object",
          "required": ["module", "urgency", "first_response_minutes"],
          "properties": {
            "module": { "type": "string" },
            "urgency": { "enum": ["low", "normal", "high", "critical"] },
            "first_response_minutes": { "type": "integer" },
            "resolution_minutes": { "type": "integer" }
          }
        }
      },
    },
    example: `[
  { "module": "Документы",  "urgency": "normal",   "first_response_minutes": 60,  "resolution_minutes": 480 },
  { "module": "Скоринг",    "urgency": "high",     "first_response_minutes": 15,  "resolution_minutes": 120 },
  { "module": "Авторизация","urgency": "critical", "first_response_minutes": 5,   "resolution_minutes": 60 }
]`,
  },

  // ───────── 5. Процессы ─────────
  {
    id: "escalation-policy",
    group: "process",
    title: "13. Регламент эскалации",
    why: "Чтобы ассистент в драфте ответа корректно говорил «передаём на L2», а не «обратитесь куда-нибудь».",
    what: "Кратко: когда L1 эскалирует наверх, по каким признакам, кому именно.",
    delivery: "Markdown-документ в KB с тегом <code>escalation</code>.",
    importable: true,
    importPath: "Через bulk-импорт KB.",
    format: "Markdown",
    schema: { kind: "markdown", content: "Произвольный текст. Лучше структурой: Триггер / Получатель / Срок / Что приложить." },
    example: `# Регламент эскалации L1 → L2

## Триггеры эскалации
- L1 не нашёл решение за 30 минут активной работы.
- Симптом повторяется у ≥3 клиентов за час (incident).
- Запрос требует доступа в админку (только L2/L3).

## Получатели
- Документы → support_l2_docs (mattermost #docs-support).
- Скоринг → risk_team (jira-проект SCORE).
- Авторизация → platform_team (oncall, pagerduty).

## Что приложить
- external_id тикета.
- Симптом одним предложением.
- Что уже пробовали.
- Скриншот / лог, если есть.`,
  },

  {
    id: "module-owners",
    group: "process",
    title: "14. Контакты владельцев модулей",
    why: "Связано с пунктом 3, но удобнее держать «лицами»: на кого пинговать в Slack, кого писать в Jira-ассайн.",
    what: "Модуль → ответственный человек/группа → канал связи.",
    delivery: "Простой JSON.",
    importable: false,
    importPath: "<code>config/module_owners.json</code>",
    format: "JSON",
    schema: {
      kind: "json-schema",
      content: {
        "type": "array",
        "items": {
          "type": "object",
          "required": ["module"],
          "properties": {
            "module": { "type": "string" },
            "tech_lead": { "type": "string" },
            "product_owner": { "type": "string" },
            "support_channel": { "type": "string", "description": "Slack/Mattermost-канал" },
            "oncall_rotation_url": { "type": "string" }
          }
        }
      },
    },
    example: `[
  {
    "module": "Документы",
    "tech_lead": "ivanov@bank.ru",
    "product_owner": "petrova@bank.ru",
    "support_channel": "#docs-support",
    "oncall_rotation_url": "https://oncall.internal/teams/docs"
  }
]`,
  },
];

const GROUP_LABELS = {
  critical: "Критично",
  kb: "База знаний",
  quality: "Качество",
  analytics: "Аналитика",
  process: "Процессы",
};

/* ----------------------- Helpers ----------------------- */

async function _copyToClipboard(text, label) {
  try {
    await navigator.clipboard.writeText(text);
    showToast(`${label || "Скопировано"} в буфер`, "success");
  } catch {
    // fallback
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand("copy");
      showToast(`${label || "Скопировано"} в буфер`, "success");
    } catch {
      showToast("Не удалось скопировать", "error");
    } finally {
      ta.remove();
    }
  }
}

function _renderSchemaBlock(schema) {
  if (!schema) return "";
  if (schema.kind === "json-schema") {
    const json = JSON.stringify(schema.content, null, 2);
    return `<div class="artifact__block">
      <div class="artifact__block-head">
        <span class="t-secondary">JSON Schema</span>
        <button type="button" class="btn btn--ghost btn--sm" data-copy="${_esc(json)}">Скопировать</button>
      </div>
      <pre class="code-block">${_esc(json)}</pre>
    </div>`;
  }
  if (schema.kind === "csv") {
    const header = schema.headers.join(";");
    const typesRows = Object.entries(schema.types || {})
      .map(([k, v]) => `<tr><td><code>${_esc(k)}</code></td><td>${_esc(v)}</td></tr>`)
      .join("");
    return `<div class="artifact__block">
      <div class="artifact__block-head">
        <span class="t-secondary">Заголовок CSV (UTF-8, разделитель «;»)</span>
        <button type="button" class="btn btn--ghost btn--sm" data-copy="${_esc(header)}">Скопировать заголовок</button>
      </div>
      <pre class="code-block">${_esc(header)}</pre>
      ${typesRows ? `<table class="table" style="margin-top: var(--space-3);">
        <thead><tr><th>Колонка</th><th>Тип / формат</th></tr></thead>
        <tbody>${typesRows}</tbody>
      </table>` : ""}
    </div>`;
  }
  if (schema.kind === "markdown") {
    return `<div class="artifact__block">
      <div class="artifact__block-head">
        <span class="t-secondary">Шаблон markdown</span>
        <button type="button" class="btn btn--ghost btn--sm" data-copy="${_esc(schema.content)}">Скопировать</button>
      </div>
      <pre class="code-block">${_esc(schema.content)}</pre>
    </div>`;
  }
  return "";
}

function _renderExample(example) {
  if (!example) return "";
  return `<div class="artifact__block">
    <div class="artifact__block-head">
      <span class="t-secondary">Пример</span>
      <button type="button" class="btn btn--ghost btn--sm" data-copy="${_esc(example)}">Скопировать</button>
    </div>
    <pre class="code-block">${_esc(example)}</pre>
  </div>`;
}

function _renderArtifact(a) {
  const importChip = a.importable
    ? `<span class="chip chip--ok">⚡ Готов импорт</span>`
    : `<span class="chip">Через конфиг / roadmap</span>`;
  const notes = (a.notes || [])
    .map((n) => `<li>${n}</li>`)
    .join("");
  return `
    <article class="artifact" data-group="${_esc(a.group)}" data-importable="${a.importable}">
      <header class="artifact__header">
        <div>
          <h3 style="margin: 0;">${_esc(a.title)}</h3>
          <div class="artifact__meta">
            <span class="chip">${_esc(GROUP_LABELS[a.group] || a.group)}</span>
            ${importChip}
            <span class="t-secondary">Формат: ${_esc(a.format)}</span>
          </div>
        </div>
      </header>
      <div class="artifact__body">
        <div class="kv">
          <div><strong>Зачем нам это:</strong> ${a.why}</div>
          <div><strong>Что именно:</strong> ${a.what}</div>
          <div><strong>Как доставить:</strong> ${a.delivery}</div>
          <div><strong>Куда:</strong> ${a.importPath}</div>
        </div>
        ${notes ? `<ul class="artifact__notes">${notes}</ul>` : ""}
        ${_renderSchemaBlock(a.schema)}
        ${_renderExample(a.example)}
      </div>
    </article>`;
}

/* ----------------------- Render ----------------------- */

export async function renderArtifacts(container) {
  const html = await (await fetch("/ui/static/pages/artifacts.html")).text();
  container.innerHTML = html;

  const slot = container.querySelector('[data-slot="artifacts"]');
  slot.innerHTML = ARTIFACTS.map(_renderArtifact).join("");

  // Copy-to-clipboard delegation
  slot.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-copy]");
    if (!btn) return;
    _copyToClipboard(btn.dataset.copy, "Шаблон");
  });

  // Filter buttons
  const filterBtns = container.querySelectorAll("[data-filter]");
  filterBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      filterBtns.forEach((b) => b.classList.remove("is-selected"));
      btn.classList.add("is-selected");
      const f = btn.dataset.filter;
      slot.querySelectorAll(".artifact").forEach((node) => {
        let show = true;
        if (f === "all") show = true;
        else if (f === "importable") show = node.dataset.importable === "true";
        else show = node.dataset.group === f;
        node.hidden = !show;
      });
    });
  });
}
