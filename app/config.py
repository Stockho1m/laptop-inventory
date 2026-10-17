"""Конфигурация из переменных окружения. Все секреты только через env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


TRUE_VALUES = {"1", "true", "yes", "y", "on"}
FALSE_VALUES = {"0", "false", "no", "n", "off"}


class ConfigError(RuntimeError):
    """Некорректная или неполная конфигурация."""


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int] = field(default_factory=frozenset)
    webapp_url: str = ""
    db_path: str = "data/inventory.db"
    port: int = 8080
    initdata_max_age: int = 86400
    bot_enabled: bool = True
    set_menu_button: bool = True

    def is_admin(self, telegram_id: int) -> bool:
        return telegram_id in self.admin_ids


def parse_admin_ids(raw: str) -> frozenset[int]:
    """'1, 2,3' -> {1, 2, 3}. Пустые элементы игнорируются."""
    ids: set[int] = set()
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            ids.add(int(chunk))
        except ValueError:
            raise ConfigError(
                f"ADMIN_IDS: '{chunk}' не является числовым Telegram ID"
            ) from None
    return frozenset(ids)


def _parse_bool(name: str, raw: str, default: bool) -> bool:
    value = raw.strip().lower()
    if not value:
        return default
    if value in TRUE_VALUES:
        return True
    if value in FALSE_VALUES:
        return False
    raise ConfigError(f"{name}: ожидалось true/false, получено '{raw}'")


def _parse_int(name: str, raw: str, default: int, minimum: int) -> int:
    value = raw.strip()
    if not value:
        return default
    try:
        number = int(value)
    except ValueError:
        raise ConfigError(f"{name}: ожидалось целое число, получено '{raw}'") from None
    if number < minimum:
        raise ConfigError(f"{name}: значение должно быть не меньше {minimum}")
    return number


def load_config(env: dict[str, str] | None = None) -> Config:
    env = dict(os.environ if env is None else env)

    bot_token = env.get("BOT_TOKEN", "").strip()
    if not bot_token:
        raise ConfigError("BOT_TOKEN не задан — заполни .env (см. .env.example)")

    admin_ids = parse_admin_ids(env.get("ADMIN_IDS", ""))
    if not admin_ids:
        raise ConfigError(
            "ADMIN_IDS не задан — укажи хотя бы один Telegram ID администратора"
        )

    return Config(
        bot_token=bot_token,
        admin_ids=admin_ids,
        webapp_url=env.get("WEBAPP_URL", "").strip().rstrip("/"),
        db_path=env.get("DB_PATH", "").strip() or "data/inventory.db",
        port=_parse_int("PORT", env.get("PORT", ""), 8080, 1),
        initdata_max_age=_parse_int(
            "INITDATA_MAX_AGE_SEC", env.get("INITDATA_MAX_AGE_SEC", ""), 86400, 1
        ),
        bot_enabled=_parse_bool("BOT_ENABLED", env.get("BOT_ENABLED", ""), True),
        set_menu_button=_parse_bool(
            "SET_MENU_BUTTON", env.get("SET_MENU_BUTTON", ""), True
        ),
    )
