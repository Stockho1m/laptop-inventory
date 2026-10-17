"""Единый формат ошибок API."""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    """Ошибка с кодом ответа и машиночитаемым кодом для фронтенда."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.extra = extra or {}

    def body(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message, **self.extra}
