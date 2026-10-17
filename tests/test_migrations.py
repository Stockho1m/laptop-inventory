"""Миграции: пересоздание laptops ради source='barcode' не теряет данные."""

from __future__ import annotations

import shutil
import sqlite3

import pytest

from app.db import MIGRATIONS_DIR, Database

INSERT = (
    "INSERT INTO laptops (serial_number, model, qr_raw, source, created_by_tg_id) "
    "VALUES (?, ?, ?, ?, ?)"
)


@pytest.fixture
def legacy_db(tmp_path) -> Database:
    """База в состоянии «применена только 0001»."""
    only_first = tmp_path / "migrations_v1"
    only_first.mkdir()
    shutil.copy(MIGRATIONS_DIR / "0001_init.sql", only_first / "0001_init.sql")

    database = Database(str(tmp_path / "legacy.db"), migrations_dir=only_first)
    assert database.migrate() == ["0001_init.sql"]

    with database.session() as conn:
        conn.execute(INSERT, ("SN-OLD", "Latitude 5420", '{"sn": "SN-OLD"}', "qr", 42))

    # До миграции barcode запрещён — иначе тест ничего не проверяет.
    with database.session() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(INSERT, ("SN-BC", None, "SN-BC", "barcode", 42))

    return Database(database.path)  # тот же файл, но с полным набором миграций


def test_barcode_migration_keeps_existing_rows(legacy_db: Database):
    assert legacy_db.migrate() == ["0002_source_barcode.sql"]

    with legacy_db.session() as conn:
        rows = conn.execute("SELECT * FROM laptops").fetchall()

    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == 1
    assert row["serial_number"] == "SN-OLD"
    assert row["model"] == "Latitude 5420"
    assert row["qr_raw"] == '{"sn": "SN-OLD"}'
    assert row["source"] == "qr"
    assert row["created_by_tg_id"] == 42


def test_barcode_allowed_after_migration(legacy_db: Database):
    legacy_db.migrate()

    with legacy_db.session() as conn:
        conn.execute(INSERT, ("SN-BC", None, "SN-BC", "barcode", 42))
        row = conn.execute(
            "SELECT source FROM laptops WHERE serial_number = 'SN-BC'"
        ).fetchone()

    assert row["source"] == "barcode"


def test_unknown_source_still_rejected_after_migration(legacy_db: Database):
    legacy_db.migrate()

    with legacy_db.session() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(INSERT, ("SN-X", None, None, "webcam", 42))


def test_constraints_and_indexes_survive_rebuild(legacy_db: Database):
    legacy_db.migrate()

    with legacy_db.session() as conn:
        # Уникальность серийного номера (вместе с COLLATE NOCASE)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(INSERT, ("sn-old", None, None, "manual", 42))

        # Ограничение статуса
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO laptops (serial_number, status, source, created_by_tg_id) "
                "VALUES (?, ?, ?, ?)",
                ("SN-ST", "сломан", "manual", 42),
            )

        # AUTOINCREMENT продолжает нумерацию, а не начинает с единицы
        conn.execute(INSERT, ("SN-NEW", None, None, "manual", 42))
        new_id = conn.execute(
            "SELECT id FROM laptops WHERE serial_number = 'SN-NEW'"
        ).fetchone()["id"]
        assert new_id == 2

        names = {row["name"] for row in conn.execute("PRAGMA index_list('laptops')")}

    assert {
        "idx_laptops_model",
        "idx_laptops_inventory_number",
        "idx_laptops_created_at",
    } <= names


def test_migrations_are_not_applied_twice(legacy_db: Database):
    assert legacy_db.migrate() == ["0002_source_barcode.sql"]
    assert legacy_db.migrate() == []


def test_rerun_after_crash_is_safe(legacy_db: Database):
    """Падение между COMMIT и записью в schema_migrations не ломает базу."""
    legacy_db.migrate()

    with legacy_db.session() as conn:
        conn.execute(
            "DELETE FROM schema_migrations WHERE name = '0002_source_barcode.sql'"
        )

    assert legacy_db.migrate() == ["0002_source_barcode.sql"]

    with legacy_db.session() as conn:
        rows = conn.execute("SELECT serial_number, source FROM laptops").fetchall()
        conn.execute(INSERT, ("SN-BC2", None, "SN-BC2", "barcode", 42))

    assert [(row["serial_number"], row["source"]) for row in rows] == [("SN-OLD", "qr")]
