"""Stable API error codes shared by HTTP, CLI and MCP adapters."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ErrorCode(str, Enum):
    INVALID_ARGUMENT = "invalid_argument"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    INTERNAL = "internal_error"


class ErrorEnvelope(BaseModel):
    code: ErrorCode
    message: str
    request_id: str
    retryable: bool = False
    details: Any = Field(default=None)


class ContractError(RuntimeError):
    """Transport-neutral application error with a stable public code."""

    def __init__(self, code: ErrorCode, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def status_for_error_code(code: ErrorCode) -> int:
    """Map a stable contract code to its HTTP representation."""
    return {
        ErrorCode.INVALID_ARGUMENT: 400,
        ErrorCode.UNAUTHORIZED: 401,
        ErrorCode.FORBIDDEN: 403,
        ErrorCode.NOT_FOUND: 404,
        ErrorCode.CONFLICT: 409,
        ErrorCode.RATE_LIMITED: 429,
        ErrorCode.UNAVAILABLE: 503,
        ErrorCode.INTERNAL: 500,
    }[code]


def error_code_for_status(status_code: int) -> ErrorCode:
    return {
        400: ErrorCode.INVALID_ARGUMENT,
        401: ErrorCode.UNAUTHORIZED,
        403: ErrorCode.FORBIDDEN,
        404: ErrorCode.NOT_FOUND,
        409: ErrorCode.CONFLICT,
        422: ErrorCode.INVALID_ARGUMENT,
        429: ErrorCode.RATE_LIMITED,
        501: ErrorCode.UNAVAILABLE,
        503: ErrorCode.UNAVAILABLE,
    }.get(status_code, ErrorCode.INTERNAL)


def status_is_retryable(status_code: int) -> bool:
    return status_code in {429, 502, 503, 504}
