"""Команды бота для управления белым списком."""

from __future__ import annotations

from app import repo
from app.bot import NOT_ADMIN, adduser_command, removeuser_command, users_command
from tests.conftest import ADMIN_ID, STRANGER_ID, auth_headers


def test_adduser_grants_access(config, database):
    answer = adduser_command(config, database, ADMIN_ID, "5555 Иван Петров")
    assert "Доступ выдан" in answer

    with database.session() as conn:
        assert repo.is_user_allowed(conn, 5555)
        row = conn.execute(
            "SELECT name, added_by FROM allowed_users WHERE telegram_id = 5555"
        ).fetchone()
    assert row["name"] == "Иван Петров"
    assert row["added_by"] == ADMIN_ID


def test_adduser_without_name(config, database):
    assert "Доступ выдан" in adduser_command(config, database, ADMIN_ID, "5556")
    with database.session() as conn:
        assert repo.is_user_allowed(conn, 5556)


def test_adduser_twice_reports_existing(config, database):
    adduser_command(config, database, ADMIN_ID, "5557")
    assert "уже был" in adduser_command(config, database, ADMIN_ID, "5557")


def test_adduser_rejects_non_numeric_id(config, database):
    answer = adduser_command(config, database, ADMIN_ID, "@username")
    assert "не похож на Telegram ID" in answer
    with database.session() as conn:
        assert repo.list_allowed_users(conn) == []


def test_adduser_without_arguments_shows_usage(config, database):
    assert "Использование: /adduser" in adduser_command(config, database, ADMIN_ID, "")


def test_adduser_is_admin_only(config, database):
    assert adduser_command(config, database, STRANGER_ID, "5558") == NOT_ADMIN
    with database.session() as conn:
        assert not repo.is_user_allowed(conn, 5558)


def test_removeuser_revokes_access(config, database):
    adduser_command(config, database, ADMIN_ID, "5559 Тест")
    assert "Доступ отозван" in removeuser_command(config, database, ADMIN_ID, "5559")
    with database.session() as conn:
        assert not repo.is_user_allowed(conn, 5559)


def test_removeuser_for_unknown_id(config, database):
    assert "не найден" in removeuser_command(config, database, ADMIN_ID, "60606")


def test_removeuser_cannot_drop_admin(config, database):
    answer = removeuser_command(config, database, ADMIN_ID, str(ADMIN_ID))
    assert "ADMIN_IDS" in answer


def test_removeuser_is_admin_only(config, database):
    adduser_command(config, database, ADMIN_ID, "5560")
    assert removeuser_command(config, database, STRANGER_ID, "5560") == NOT_ADMIN
    with database.session() as conn:
        assert repo.is_user_allowed(conn, 5560)


def test_users_lists_admins_and_allowed(config, database):
    empty = users_command(config, database, ADMIN_ID)
    assert "Список доступа пуст" in empty
    assert str(ADMIN_ID) in empty

    adduser_command(config, database, ADMIN_ID, "5561 Мария")
    answer = users_command(config, database, ADMIN_ID)
    assert "5561" in answer
    assert "Мария" in answer


def test_users_is_admin_only(config, database):
    assert users_command(config, database, STRANGER_ID) == NOT_ADMIN


def test_access_granted_by_bot_works_in_api(client, config, database):
    headers = auth_headers(STRANGER_ID)
    assert client.get("/api/me", headers=headers).status_code == 403

    adduser_command(config, database, ADMIN_ID, f"{STRANGER_ID} Новый сотрудник")
    assert client.get("/api/me", headers=headers).status_code == 200

    removeuser_command(config, database, ADMIN_ID, str(STRANGER_ID))
    assert client.get("/api/me", headers=headers).status_code == 403
