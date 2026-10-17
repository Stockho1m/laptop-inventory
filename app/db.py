"""Подключение к SQLite и минимальный раннер миграций."""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def search_key(value: object) -> str | None:
    """Ключ поиска без учёта регистра и «ё». Встроенные LIKE и lower() в SQLite
    приводят к нижнему регистру только латиницу, а имена сотрудников — кириллица."""
    if value is None:
        return None
    return str(value).casefold().replace("ё", "е")


class Database:
    def __init__(self, path: str, migrations_dir: Path = MIGRATIONS_DIR) -> None:
        self.path = path
        self.migrations_dir = migrations_dir

    def connect(self) -> sqlite3.Connection:
        """Новое соединение на запрос: открытие SQLite дешёвое, а так нет
        общего состояния между потоками пула FastAPI."""
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.create_function("search_key", 1, search_key, deterministic=True)
        return conn

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        """Соединение, которое гарантированно закрывается."""
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    def migrate(self) -> list[str]:
        """Применяет неприменённые .sql по порядку. Возвращает их имена."""
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent:
            os.makedirs(parent, exist_ok=True)

        applied: list[str] = []
        with closing(self.connect()) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    name       TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            done = {
                row["name"] for row in conn.execute("SELECT name FROM schema_migrations")
            }
            for sql_file in sorted(self.migrations_dir.glob("*.sql")):
                if sql_file.name in done:
                    continue
                log.info("Применяю миграцию %s", sql_file.name)
                conn.executescript(sql_file.read_text(encoding="utf-8"))
                conn.execute(
                    "INSERT INTO schema_migrations (name) VALUES (?)", (sql_file.name,)
                )
                applied.append(sql_file.name)
        return applied
