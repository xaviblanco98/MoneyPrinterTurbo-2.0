"""Declarative base and shared column types."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String, TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Store UTC datetimes in SQLite and always return timezone-aware values."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


class EnumStr(TypeDecorator):
    """Store a str-Enum as its value and always return the Enum member on read."""

    impl = String
    cache_ok = True

    def __init__(self, enum_cls, length: int = 32):
        super().__init__(length)
        self.enum_cls = enum_cls

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return getattr(value, "value", value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        try:
            return self.enum_cls(value)
        except ValueError:
            return value


class Base(DeclarativeBase):
    pass


class IdMixin:
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
