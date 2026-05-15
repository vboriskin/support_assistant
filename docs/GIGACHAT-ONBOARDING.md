# GigaChat onboarding — что просить у безопасности

Документ для технического лида проекта. Объясняет, что и как запрашивать у команды банковской безопасности перед запуском в продуктивной зоне.

## TL;DR

Чтобы запустить пилот, нужно получить:

1. **GigaChat client_id / client_secret** (OAuth client_credentials) с областью `GIGACHAT_API_CORP`.
2. **Whitelist сетевого доступа** с пилотного сервера к двум хостам Сбера.
3. **CA-bundle** (если корп-прокси режет TLS).
4. **Договорённость о ротации** secret'а — раз в N дней.

Остальное (PII, изоляция данных) система обеспечивает сама.

## 1. OAuth credentials

### Что запросить

В корпоративном портале GigaChat / у владельца продукта:

- **Тип клиента**: `client_credentials` (не `authorization_code`).
- **Scope**: `GIGACHAT_API_CORP`.
- **Доступные модели**: минимум `GigaChat-Max` (для основного ассистента и судей).
- **Лимиты**: 30 RPM на пользователя — достаточно для пилота из 2–3 операторов.
- **Назначение**: «Внутренний RAG-ассистент 1-й линии поддержки кредитного фронта».

### Что получите

Пара `client_id` (UUID-like) + `client_secret` (base64-строка). Класть в `.env`:

```
GIGACHAT_CLIENT_ID=<uuid>
GIGACHAT_CLIENT_SECRET=<base64>
GIGACHAT_SCOPE=GIGACHAT_API_CORP
```

### Куда они НЕ попадают

- В git — `.env` в `.gitignore` (`.env.example` — только шаблон).
- В логи — settings используют `SecretStr`, error-paths проходят через `redact_secrets`.
- В клиентский ответ — никогда не отдаём ничего, что приходит из `Authorization` или `RqUID` обратно пользователю.

## 2. Сетевой доступ

С пилотного сервера должны быть доступны два хоста (порты — стандартные):

| Хост | Назначение | Порт |
|---|---|---|
| `ngw.devices.sberbank.ru` | OAuth-эндпоинт (`/api/v2/oauth`) | 9443 |
| `gigachat.devices.sberbank.ru` | Chat API (`/api/v1/chat/completions`) | 443 |

В контурах со строгим egress — попросить **firewall whitelist** на `gigachat.devices.sberbank.ru:443` и `ngw.devices.sberbank.ru:9443`. Прочие хосты блокировать: код приложения дополнительно проверяет хост через `core/security.is_allowed_llm_host` — приложение упадёт на старте, если кто-то подменит `GIGACHAT_BASE_URL` в `.env`.

## 3. TLS / CA-bundle

В большинстве контуров — нужно указать корпоративный CA-bundle:

```
GIGACHAT_VERIFY_SSL=true
GIGACHAT_CA_BUNDLE_PATH=/etc/ssl/certs/sber-ca-bundle.pem
```

Если в контуре self-signed без CA — допустимо `GIGACHAT_VERIFY_SSL=false`, но только для тестового стенда. В prod — всегда с проверкой.

CA-bundle обычно доступен у команды инфраструктуры. Если нужно «достать самим» — `openssl s_client -showcerts -connect gigachat.devices.sberbank.ru:443` и сохранить корневой/промежуточный сертификат.

## 4. Ротация credentials

Политика банка обычно требует ротации client_secret каждые 60–180 дней.

Процесс:

1. Получить новый `client_secret` через корпоративный портал.
2. На сервере: `vim .env` → обновить `GIGACHAT_CLIENT_SECRET`.
3. `systemctl restart support-assistant` — приложение перечитает.
4. После ротации старый secret должен быть отозван через тот же портал.

Сменить ID можно так же, но это редкая операция (обычно — только при смене проектного аккаунта).

## 5. Что НЕ нужно просить

- **Не нужны admin-права** в GigaChat — обычного `client_credentials` достаточно.
- **Не нужен публичный IP** — это исходящие запросы, входящих не делаем.
- **Не нужен webhook** — стрим обычный HTTPS chunked/SSE.
- **Не нужен доступ к пользовательским данным GigaChat** — мы шлём только заявки, маскированные от PII.

## 6. PII и compliance

Это **не вопрос к безопасности GigaChat** (там данные стираются после генерации). Это вопрос к нам:

- Перед каждым LLM-вызовом текст проходит `PIIMaskingPipeline` (`core/pii/`).
- Маскируются 13 типов PII (см. `docs/08-PII-MASKING.md` и `tests/fixtures/golden_pii.json`).
- В `PII_STRICT_MODE=true` любая остаточная PII блокирует запрос/индексацию.
- Аудит-чек-лист: `docs/SECURITY-CHECKLIST.md`.

Если безопасность банка попросит подтверждение «ничего лишнего не уходит к Сберу» — показать им `pytest tests/unit/test_pii_masking.py` (golden-набор) и формат payload (можно дампнуть тестовый запрос: `LLM_PROVIDER=mock python -m scripts.run_evals --sample 1` → смотреть `llm_call_logs.prompt_preview`).

## 7. Альтернатива: YandexGPT / OpenAI-compatible

Если по каким-то причинам GigaChat недоступен, провайдер переключается одной переменной:

```
LLM_PROVIDER=openai_compatible
OPENAI_BASE_URL=https://internal-gw.bank.local/v1
OPENAI_API_KEY=<key>
OPENAI_MODEL=Qwen2.5-32B-Instruct
SECURITY_ALLOWED_LLM_HOSTS=internal-gw.bank.local
```

Адаптер `OpenAICompatibleClient` работает с любым OpenAI-совместимым шлюзом. Whitelist хостов аналогичный — добавьте свой через `SECURITY_ALLOWED_LLM_HOSTS`.

## Памятка по реквизитам в `.env`

```
LLM_PROVIDER=gigachat
GIGACHAT_BASE_URL=https://gigachat.devices.sberbank.ru/api/v1
GIGACHAT_AUTH_URL=https://ngw.devices.sberbank.ru:9443/api/v2/oauth
GIGACHAT_CLIENT_ID=<UUID>
GIGACHAT_CLIENT_SECRET=<BASE64>
GIGACHAT_SCOPE=GIGACHAT_API_CORP
GIGACHAT_MODEL_PRIMARY=GigaChat-Max
GIGACHAT_VERIFY_SSL=true
GIGACHAT_CA_BUNDLE_PATH=/etc/ssl/certs/sber-ca-bundle.pem
LLM_TIMEOUT_SECONDS=60
LLM_MAX_RETRIES=3
LLM_BUDGET_PER_USER_DAILY=300
```

Этого достаточно, чтобы запустить ингест и ассистента в prod-режиме.
