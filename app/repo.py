"""Все SQL-запросы приложения."""

from __future__ import annotations

import json
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


# --- журнал действий -----------------------------------------------------

AUDIT_ACTIONS = ("laptop_create", "laptop_update", "access_grant", "access_revoke")

# Поля ноутбука, попадающие в журнал. qr_raw не пишем: он неизменяем, а в
# истории занимал бы много места без пользы.
AUDITED_FIELDS = (
    "manufacturer",
    "model",
    "serial_number",
    "inventory_number",
    "status",
    "assigned_to",
    "location",
    "notes",
    "source",
)


def diff_fields(
    before: dict[str, Any] | None, after: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """Какие из отслеживаемых полей изменились: {поле: {from, to}}.

    before=None — создание записи, тогда from у всех полей null, а поля с
    пустым значением пропускаются.
    """
    changes: dict[str, dict[str, Any]] = {}
    for field in AUDITED_FIELDS:
        if field not in after:
            continue
        new_value = after.get(field)
        if before is None:
            if new_value not in (None, ""):
                changes[field] = {"from": None, "to": new_value}
            continue
        old_value = before.get(field)
        if old_value != new_value:
            changes[field] = {"from": old_value, "to": new_value}
    return changes


def write_audit(
    conn: sqlite3.Connection,
    *,
    actor_tg_id: int,
    actor_name: str | None,
    action: str,
    entity_type: str,
    entity_id: str | int,
    changes: dict[str, Any] | None = None,
) -> None:
    if action not in AUDIT_ACTIONS:
        raise ValueError(f"неизвестное действие журнала {action!r}")
    conn.execute(
        """
        INSERT INTO audit_log
            (actor_tg_id, actor_name, action, entity_type, entity_id, changes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            actor_tg_id,
            actor_name,
            action,
            entity_type,
            str(entity_id),
            json.dumps(changes, ensure_ascii=False) if changes else None,
        ),
    )


def laptop_history(
    conn: sqlite3.Connection, laptop_id: int, limit: int = 100
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM audit_log
        WHERE entity_type = 'laptop' AND entity_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (str(laptop_id), limit),
    ).fetchall()


def assignment_history(
    conn: sqlite3.Connection, laptop_id: int
) -> list[dict[str, Any]]:
    """Прошлые владельцы ноутбука, выведенные из журнала. Свежие первыми.

    Отдельного хранилища не нужно: каждая смена поля assigned_to уже записана
    в audit_log вместе со старым значением. since=None означает, что начало
    владения неизвестно — ноутбук сменил владельца раньше, чем появился журнал.
    """
    rows = conn.execute(
        """
        SELECT changes, created_at FROM audit_log
        WHERE entity_type = 'laptop' AND entity_id = ?
        ORDER BY id
        """,
        (str(laptop_id),),
    ).fetchall()

    holders: list[dict[str, Any]] = []
    since: str | None = None
    for row in rows:
        if not row["changes"]:
            continue
        try:
            changes = json.loads(row["changes"])
        except json.JSONDecodeError:
            continue
        change = changes.get("assigned_to")
        if not isinstance(change, dict):
            continue

        previous = change.get("from")
        if previous:
            holders.append(
                {"name": previous, "since": since, "until": row["created_at"]}
            )
        # Новый владелец получил ноутбук в момент этой записи; если поле
        # очистили, то следующий владелец начнёт с неизвестной даты.
        since = row["created_at"] if change.get("to") else None

    holders.reverse()
    return holders


def audit_entries(
    conn: sqlite3.Connection, limit: int = 100, offset: int = 0
) -> tuple[list[sqlite3.Row], int]:
    total = conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"]
    rows = conn.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return rows, total


# --- ноутбуки ------------------------------------------------------------


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
