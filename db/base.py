"""Базовый SQLAlchemy-класс для всех ORM-моделей."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Корневой класс для ORM. Все таблицы наследуются от него."""
