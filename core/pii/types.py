"""Типы PII и общие data-классы маскирования."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class PIIType(StrEnum):
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
    """Найденное совпадение."""

    pii_type: PIIType
    original: str
    start: int
    end: int
    confidence: float = 1.0


class PIIRemainsError(Exception):
    """Strict-mode не пропустил остаточную PII после маскирования."""
