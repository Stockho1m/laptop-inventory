"""Все SQL-запросы приложения."""

from __future__ import annotations

import sqlite3
from typing import Any

from .db import search_key

LAPTOP_COLUMNS = (
    "manufacturer",
    "model",
    "serial_number",
    "inventory_number",
    "status",
    "assigned_to",
    "location",
    "notes",
    "qr_raw",
    "source",
)

EDITABLE_COLUMNS = (
    "manufacturer",
    "model",
    "serial_number",
    "inventory_number",
    "status",
    "assigned_to",
    "location",
    "notes",
)

# Где искать в зависимости от выбранного режима поиска.
SEARCH_COLUMNS: dict[str, tuple[str, ...]] = {
    "all": ("serial_number", "assigned_to", "model", "inventory_number"),
    "serial": ("serial_number",),
    "employee": ("assigned_to",),
}

# Порядок групп при сортировке по статусу: сначала то, что в обороте.
STATUS_ORDER = ("in_use", "in_stock", "repair", "retired")


class DuplicateSerialError(Exception):
    """Серийный номер уже занят другой записью."""

    def __init__(self, serial_number: str) -> None:
        super().__init__(serial_number)
        self.serial_number = serial_number


# --- белый список --------------------------------------------------------


def is_user_allowed(conn: sqlite3.Connection, telegram_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM allowed_users WHERE telegram_id = ?", (telegram_id,)
    ).fetchone()
    return row is not None


def add_allowed_user(
    conn: sqlite3.Connection, telegram_id: int, name: str | None, added_by: int
) -> bool:
    """True — добавлен, False — уже был в списке (имя тогда обновляется)."""
    cursor = conn.execute(
        "INSERT OR IGNORE INTO allowed_users (telegram_id, name, added_by) VALUES (?, ?, ?)",
        (telegram_id, name, added_by),
    )
    if cursor.rowcount:
        return True
    if name:
        conn.execute(
            "UPDATE allowed_users SET name = ? WHERE telegram_id = ?",
            (name, telegram_id),
        )
    return False


def remove_allowed_user(conn: sqlite3.Connection, telegram_id: int) -> bool:
    cursor = conn.execute(
        "DELETE FROM allowed_users WHERE telegram_id = ?", (telegram_id,)
    )
    return bool(cursor.rowcount)


def list_allowed_users(conn: sqlite3.Connection, limit: int = 200) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT telegram_id, name, added_by, created_at
        FROM allowed_users
        ORDER BY created_at, telegram_id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


# --- ноутбуки ------------------------------------------------------------


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_laptops(
    conn: sqlite3.Connection,
    query: str = "",
    limit: int = 50,
    offset: int = 0,
    *,
    by: str = "all",
    status: str | None = None,
    sort: str = "new",
) -> tuple[list[sqlite3.Row], int, dict[str, int]]:
    """Поиск с фильтром по статусу.

    Возвращает страницу, общее число найденных и разбивку по статусам. Разбивка
    учитывает поиск, но не фильтр по статусу — она нужна для счётчиков фильтра.
    """
    if by not in SEARCH_COLUMNS:
        raise ValueError(f"неизвестный режим поиска {by!r}")

    query = query.strip()
    search_where: list[str] = []
    search_params: list[Any] = []
    if query:
        pattern = f"%{_escape_like(search_key(query))}%"
        columns = SEARCH_COLUMNS[by]
        conditions = " OR ".join(
            f"search_key({column}) LIKE ? ESCAPE '\\'" for column in columns
        )
        search_where.append(f"({conditions})")
        search_params.extend([pattern] * len(columns))

    counts = {code: 0 for code in STATUS_ORDER}
    search_sql = f"WHERE {' AND '.join(search_where)}" if search_where else ""
    for row in conn.execute(
        f"SELECT status, COUNT(*) AS n FROM laptops {search_sql} GROUP BY status",
        search_params,
    ):
        counts[row["status"]] = row["n"]

    where = list(search_where)
    params = list(search_params)
    if status:
        where.append("status = ?")
        params.append(status)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM laptops {where_sql}", params
    ).fetchone()["n"]

    order = "datetime(created_at) DESC, id DESC"
    if sort == "status":
        # Коды статусов — закрытый список констант, подставлять их в SQL безопасно.
        ranks = " ".join(
            f"WHEN '{code}' THEN {rank}" for rank, code in enumerate(STATUS_ORDER)
        )
        order = f"CASE status {ranks} ELSE {len(STATUS_ORDER)} END, {order}"

    rows = conn.execute(
        f"""
        SELECT * FROM laptops
        {where_sql}
        ORDER BY {order}
        LIMIT ? OFFSET ?
        """,
        [*params, limit, offset],
    ).fetchall()
    return rows, total, counts


SUGGESTABLE_COLUMNS = ("manufacturer", "model", "assigned_to")


def distinct_values(
    conn: sqlite3.Connection, column: str, limit: int = 500
) -> list[str]:
    """Уникальные непустые значения колонки — для подсказок в форме."""
    if column not in SUGGESTABLE_COLUMNS:
        # Имя колонки подставляется в SQL, поэтому список закрытый.
        raise ValueError(f"подсказки не поддерживаются для колонки {column!r}")

    rows = conn.execute(
        f"""
        SELECT DISTINCT {column} AS value
        FROM laptops
        WHERE {column} IS NOT NULL AND {column} <> ''
        ORDER BY {column} COLLATE NOCASE
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [row["value"] for row in rows]


def get_laptop(conn: sqlite3.Connection, laptop_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM laptops WHERE id = ?", (laptop_id,)).fetchone()


def get_laptop_by_serial(
    conn: sqlite3.Connection, serial_number: str
) -> sqlite3.Row | None:
    """Колонка объявлена COLLATE NOCASE, поэтому регистр не важен."""
    return conn.execute(
        "SELECT * FROM laptops WHERE serial_number = ?", (serial_number,)
    ).fetchone()


def create_laptop(
    conn: sqlite3.Connection, data: dict[str, Any], created_by_tg_id: int
) -> sqlite3.Row:
    columns = [*LAPTOP_COLUMNS, "created_by_tg_id"]
    placeholders = ", ".join("?" for _ in columns)
    values = [data.get(column) for column in LAPTOP_COLUMNS] + [created_by_tg_id]
    try:
        cursor = conn.execute(
            f"INSERT INTO laptops ({', '.join(columns)}) VALUES ({placeholders})",
            values,
        )
    except sqlite3.IntegrityError as exc:
        if "serial_number" in str(exc):
            raise DuplicateSerialError(str(data.get("serial_number", ""))) from None
        raise
    row = get_laptop(conn, int(cursor.lastrowid))
    assert row is not None
    return row


def update_laptop(
    conn: sqlite3.Connection, laptop_id: int, data: dict[str, Any]
) -> sqlite3.Row | None:
    fields = {key: value for key, value in data.items() if key in EDITABLE_COLUMNS}
    if not fields:
        return get_laptop(conn, laptop_id)

    assignments = ", ".join(f"{column} = ?" for column in fields)
    try:
        cursor = conn.execute(
            f"UPDATE laptops SET {assignments}, updated_at = datetime('now') WHERE id = ?",
            [*fields.values(), laptop_id],
        )
    except sqlite3.IntegrityError as exc:
        if "serial_number" in str(exc):
            raise DuplicateSerialError(str(fields.get("serial_number", ""))) from None
        raise
    if not cursor.rowcount:
        return None
    return get_laptop(conn, laptop_id)
