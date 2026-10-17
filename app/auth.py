"""Проверка Telegram.WebApp.initData и белого списка.

Алгоритм — официальный (Telegram Mini Apps, "Validating data received via
the Mini App"):

    secret_key       = HMAC_SHA256(key="WebAppData", msg=<bot_token>)
    data_check_string = "\\n".join(f"{k}={v}" for k, v in sorted(fields))
    hash             = hex(HMAC_SHA256(key=secret_key, msg=data_check_string))

Из data_check_string исключается ТОЛЬКО поле hash. Поле signature (подпись
Ed25519 для сторонней валидации) в строку входит — его исключают лишь при
проверке по публичному ключу Telegram, которую мы не используем.

Клиентскому initDataUnsafe и любым ID из тела запроса доверять нельзя:
единственный источник личности — подписанная строка initData.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

from fastapi import Request

from .errors import ApiError

INIT_DATA_HEADER = "X-Telegram-Init-Data"

# Допуск на расхождение часов клиента и сервера для auth_date из будущего.
CLOCK_SKEW_SEC = 300


class InitDataError(Exception):
    """initData отсутствует, подделана или просрочена."""


@dataclass(frozen=True)
class TelegramUser:
    telegram_id: int
    name: str
    auth_date: int


@dataclass(frozen=True)
class AuthUser:
    telegram_id: int
    name: str
    is_admin: bool


def _display_name(user: dict) -> str:
    parts = [str(user.get("first_name") or "").strip(), str(user.get("last_name") or "").strip()]
    full = " ".join(part for part in parts if part)
    if full:
        return full
    username = str(user.get("username") or "").strip()
    if username:
        return f"@{username}"
    return f"id{user.get('id')}"


def verify_init_data(
    init_data: str,
    bot_token: str,
    max_age: int,
    now: float | None = None,
) -> TelegramUser:
    """Проверяет подпись и свежесть initData. Возвращает пользователя из подписанных данных."""
    if not init_data or not init_data.strip():
        raise InitDataError("initData отсутствует")

    fields: dict[str, str] = {}
    for key, value in parse_qsl(init_data, keep_blank_values=True):
        if key in fields:
            raise InitDataError(f"initData содержит дублирующееся поле '{key}'")
        fields[key] = value

    received_hash = fields.pop("hash", "")
    if not received_hash:
        raise InitDataError("в initData нет поля hash")

    data_check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected_hash, received_hash.strip().lower()):
        raise InitDataError("подпись initData не совпадает")

    raw_auth_date = fields.get("auth_date", "")
    try:
        auth_date = int(raw_auth_date)
    except ValueError:
        raise InitDataError("некорректное поле auth_date") from None

    current = time.time() if now is None else now
    age = current - auth_date
    if age > max_age:
        raise InitDataError("initData просрочена, переоткрой приложение")
    if age < -CLOCK_SKEW_SEC:
        # Подпись верна, значит auth_date поставил Telegram: отстают часы сервера.
        raise InitDataError(
            f"Часы сервера отстают от Telegram примерно на {round(-age / 60)} мин. "
            "Синхронизируй время на сервере и переоткрой приложение."
        )

    raw_user = fields.get("user", "")
    if not raw_user:
        raise InitDataError("в initData нет данных пользователя")
    try:
        user = json.loads(raw_user)
    except json.JSONDecodeError:
        raise InitDataError("поле user не является JSON") from None
    if not isinstance(user, dict):
        raise InitDataError("поле user имеет неверный формат")

    try:
        telegram_id = int(user["id"])
    except (KeyError, TypeError, ValueError):
        raise InitDataError("в данных пользователя нет корректного id") from None

    return TelegramUser(
        telegram_id=telegram_id, name=_display_name(user), auth_date=auth_date
    )


def require_user(request: Request) -> AuthUser:
    """Зависимость FastAPI: подпись -> белый список. Иначе 401 / 403."""
    from . import repo  # локальный импорт, чтобы не было циклической зависимости

    config = request.app.state.config
    database = request.app.state.db

    try:
        telegram_user = verify_init_data(
            request.headers.get(INIT_DATA_HEADER, ""),
            config.bot_token,
            config.initdata_max_age,
        )
    except InitDataError as exc:
        raise ApiError(401, "unauthorized", str(exc)) from None

    is_admin = config.is_admin(telegram_user.telegram_id)
    if not is_admin:
        with database.session() as conn:
            allowed = repo.is_user_allowed(conn, telegram_user.telegram_id)
        if not allowed:
            raise ApiError(
                403,
                "forbidden",
                "Нет доступа. Передай свой Telegram ID администратору.",
                {"telegram_id": telegram_user.telegram_id},
            )

    return AuthUser(
        telegram_id=telegram_user.telegram_id,
        name=telegram_user.name,
        is_admin=is_admin,
    )
