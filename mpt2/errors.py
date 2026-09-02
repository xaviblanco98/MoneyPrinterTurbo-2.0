"""Structured errors shared across mpt2.

Every persisted failure carries a machine-readable ``code``, a human
``message``, the ``module`` that raised it and the UTC instant it happened.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ErrorInfo(BaseModel):
    """Serializable error record stored on jobs and returned by the API."""

    code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=4000)
    module: str = Field(min_length=1, max_length=200)
    occurred_at: datetime = Field(default_factory=utcnow)


class MPT2Error(Exception):
    """Base class for errors raised by mpt2 code."""

    code = "mpt2_error"

    def __init__(
        self, message: str, *, code: str | None = None, module: str | None = None
    ):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.module = module or self.__class__.__module__

    def to_info(self) -> ErrorInfo:
        return ErrorInfo(code=self.code, message=self.message, module=self.module)


class SettingsError(MPT2Error):
    code = "settings_invalid"


class NotFoundError(MPT2Error):
    code = "not_found"


class InvalidTransitionError(MPT2Error):
    code = "invalid_transition"


class StageError(MPT2Error):
    """Raised by a stage handler to fail a job with a specific code.

    ``retryable=False`` moves the job straight to ``failed`` without using the
    remaining attempts (for example: invalid input that will never succeed).
    """

    code = "stage_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        module: str | None = None,
        retryable: bool = True,
    ):
        super().__init__(message, code=code, module=module)
        self.retryable = retryable
