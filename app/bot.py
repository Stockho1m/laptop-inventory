"""Телеграм-бот: выдача и отзыв доступа к Mini App. Только для админов.

Логика команд вынесена в чистые функции (их же проверяют тесты), обработчики
aiogram только передают аргументы и отправляют ответ.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import MenuButtonWebApp, Message, WebAppInfo

from . import repo
from .config import Config
from .db import Database

log = logging.getLogger(__name__)

NOT_ADMIN = "Команда доступна только администратору."
MAX_NAME_LEN = 100
USERS_LIMIT = 200


def _parse_telegram_id(raw: str) -> int:
    value = int(raw.strip())
    if value <= 0:
        raise ValueError
    return value


def adduser_command(config: Config, database: Database, actor_id: int, args: str) -> str:
    if not config.is_admin(actor_id):
        return NOT_ADMIN

    parts = (args or "").strip().split(maxsplit=1)
    if not parts:
        return "Использование: /adduser <telegram_id> [имя]"
    try:
        telegram_id = _parse_telegram_id(parts[0])
    except ValueError:
        return f"'{parts[0]}' не похож на Telegram ID. Нужно целое число, например 123456789."

    name = parts[1].strip()[:MAX_NAME_LEN] if len(parts) > 1 else None

    with database.session() as conn:
        added = repo.add_allowed_user(conn, telegram_id, name, actor_id)

    who = f"{telegram_id} ({name})" if name else str(telegram_id)
    if added:
        return f"Доступ выдан: {who}"
    return f"{who} уже был в списке доступа."


def removeuser_command(
    config: Config, database: Database, actor_id: int, args: str
) -> str:
    if not config.is_admin(actor_id):
        return NOT_ADMIN

    raw = (args or "").strip()
    if not raw:
        return "Использование: /removeuser <telegram_id>"
    try:
        telegram_id = _parse_telegram_id(raw.split()[0])
    except ValueError:
        return f"'{raw.split()[0]}' не похож на Telegram ID."

    with database.session() as conn:
        removed = repo.remove_allowed_user(conn, telegram_id)

    if not removed:
        if config.is_admin(telegram_id):
            return (
                f"{telegram_id} — администратор (ADMIN_IDS), его доступ "
                "командой не отзывается. Убери ID из переменной окружения."
            )
        return f"{telegram_id} не найден в списке доступа."
    return f"Доступ отозван: {telegram_id}"


def users_command(config: Config, database: Database, actor_id: int) -> str:
    if not config.is_admin(actor_id):
        return NOT_ADMIN

    with database.session() as conn:
        rows = repo.list_allowed_users(conn, USERS_LIMIT)

    admins = ", ".join(str(admin_id) for admin_id in sorted(config.admin_ids))
    header = f"Администраторы (ADMIN_IDS): {admins}"
    if not rows:
        return f"{header}\n\nСписок доступа пуст. Добавь: /adduser <telegram_id> [имя]"

    lines = [f"{header}", "", f"Доступ выдан ({len(rows)}):"]
    for row in rows:
        name = row["name"] or "без имени"
        lines.append(f"• {row['telegram_id']} — {name} (добавил {row['added_by']})")
    return "\n".join(lines)


def build_router(config: Config, database: Database) -> Router:
    router = Router(name="access")

    @router.message(Command("adduser"))
    async def handle_adduser(message: Message, command: CommandObject) -> None:
        actor_id = message.from_user.id if message.from_user else 0
        await message.answer(
            adduser_command(config, database, actor_id, command.args or "")
        )

    @router.message(Command("removeuser"))
    async def handle_removeuser(message: Message, command: CommandObject) -> None:
        actor_id = message.from_user.id if message.from_user else 0
        await message.answer(
            removeuser_command(config, database, actor_id, command.args or "")
        )

    @router.message(Command("users"))
    async def handle_users(message: Message) -> None:
        actor_id = message.from_user.id if message.from_user else 0
        await message.answer(users_command(config, database, actor_id))

    return router


class BotRunner:
    """Long polling в фоне того же процесса, что и веб-приложение."""

    def __init__(self, config: Config, database: Database) -> None:
        self.config = config
        self.bot = Bot(token=config.bot_token)
        self.dispatcher = Dispatcher()
        self.dispatcher.include_router(build_router(config, database))
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self.config.set_menu_button and self.config.webapp_url:
            await self._set_menu_button()
        self._task = asyncio.create_task(self._run(), name="bot-polling")

    async def _set_menu_button(self) -> None:
        try:
            await self.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="Инвентаризация",
                    web_app=WebAppInfo(url=self.config.webapp_url),
                )
            )
            log.info("Кнопка меню бота привязана к %s", self.config.webapp_url)
        except Exception:
            log.exception(
                "Не удалось привязать кнопку меню к %s — настрой её в BotFather "
                "или проверь WEBAPP_URL (нужен публичный https)",
                self.config.webapp_url,
            )

    async def _run(self) -> None:
        try:
            await self.bot.delete_webhook(drop_pending_updates=True)
            await self.dispatcher.start_polling(self.bot, handle_signals=False)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Падение бота не должно ронять веб-приложение.
            log.exception("Бот остановлен из-за ошибки")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self.bot.session.close()
