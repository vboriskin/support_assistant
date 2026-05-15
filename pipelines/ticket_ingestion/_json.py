"""Утилита: вытащить первый JSON-объект из ответа LLM.

Модели любят оборачивать ответ в ``` ```json ... ``` ``` или приписать
комментарий. Парсер выбирает фрагмент между первой ``{`` и парной ``}`` —
этого хватает в 99% случаев. На совсем мусорном ответе вернёт исходную строку,
тогда дальнейший ``model_validate_json`` корректно упадёт ``ValidationError``.
"""

from __future__ import annotations


def extract_json_object(s: str) -> str:
    s = s.strip()
    # Срезаем ``` ```json ... ``` ```
    if s.startswith("```"):
        s = s.lstrip("`")
        if s.startswith("json"):
            s = s[4:]
        if s.endswith("```"):
            s = s[:-3]
        s = s.strip()
    # Если есть текст вокруг JSON — выделим самый внешний {...}
    if not s.startswith("{"):
        i = s.find("{")
        if i >= 0:
            s = s[i:]
    depth = 0
    end = -1
    in_string = False
    escape = False
    for idx, ch in enumerate(s):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = idx + 1
                break
    if end > 0:
        return s[:end]
    return s
