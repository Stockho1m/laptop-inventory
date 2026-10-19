"""Журнал действий: кто, когда и что изменил."""

from __future__ import annotations

from app.bot import adduser_command, removeuser_command
from tests.conftest import ADMIN_ID, ALLOWED_ID, STRANGER_ID, auth_headers


def create(client, **fields):
    payload = {"serial_number": "SN-1", "source": "manual"}
    payload.update(fields)
    return client.post("/api/laptops", json=payload)


def history(client, laptop_id):
    response = client.get(f"/api/laptops/{laptop_id}/history")
    assert response.status_code == 200
    return response.json()["items"]


# --- ноутбуки ------------------------------------------------------------


def test_create_is_logged_with_author(allowed_client):
    laptop = create(allowed_client, serial_number="SN-A", model="FLMH-X").json()

    entries = history(allowed_client, laptop["id"])
    assert len(entries) == 1
    entry = entries[0]
    assert entry["action"] == "laptop_create"
    assert entry["actor_tg_id"] == ALLOWED_ID
    assert entry["actor_name"] == "Тест Юзер"
    assert entry["entity_type"] == "laptop"
    assert entry["changes"]["serial_number"] == {"from": None, "to": "SN-A"}
    assert entry["changes"]["model"] == {"from": None, "to": "FLMH-X"}
    # Незаполненные поля в историю создания не попадают
    assert "location" not in entry["changes"]


def test_update_logs_only_changed_fields(allowed_client):
    laptop = create(allowed_client, serial_number="SN-B", model="Старая").json()
    allowed_client.patch(
        f"/api/laptops/{laptop['id']}",
        json={"model": "Новая", "status": "in_use", "location": None},
    )

    entries = history(allowed_client, laptop["id"])
    assert [e["action"] for e in entries] == ["laptop_update", "laptop_create"]

    changes = entries[0]["changes"]
    assert changes["model"] == {"from": "Старая", "to": "Новая"}
    assert changes["status"] == {"from": "in_stock", "to": "in_use"}
    # location как было пустым, так и осталось — в журнал не попадает
    assert "location" not in changes
    assert "serial_number" not in changes


def test_unchanged_save_writes_nothing(allowed_client):
    laptop = create(allowed_client, serial_number="SN-C", model="Та же").json()

    response = allowed_client.patch(
        f"/api/laptops/{laptop['id']}", json={"model": "Та же"}
    )
    assert response.status_code == 200
    # Запись не трогали: ни истории, ни нового updated_at
    assert response.json()["updated_at"] == laptop["updated_at"]
    assert [e["action"] for e in history(allowed_client, laptop["id"])] == [
        "laptop_create"
    ]


def test_failed_create_leaves_no_trace(allowed_client):
    first = create(allowed_client, serial_number="SN-D").json()
    assert create(allowed_client, serial_number="sn-d").status_code == 409

    # Откат транзакции: запись о неудачной попытке в журнал не попала
    entries = history(allowed_client, first["id"])
    assert len(entries) == 1


def test_history_of_missing_laptop_is_404(allowed_client):
    assert allowed_client.get("/api/laptops/999/history").status_code == 404


def test_history_requires_access(client):
    response = client.get("/api/laptops/1/history", headers=auth_headers(STRANGER_ID))
    assert response.status_code == 403


# --- общий журнал --------------------------------------------------------


def test_audit_journal_is_admin_only(allowed_client):
    assert allowed_client.get("/api/audit").status_code == 403


def test_admin_sees_journal(client, database):
    client.headers.update(auth_headers(ADMIN_ID))
    create(client, serial_number="SN-E")

    payload = client.get("/api/audit").json()
    assert payload["total"] == 1
    assert payload["items"][0]["action"] == "laptop_create"
    assert payload["items"][0]["actor_tg_id"] == ADMIN_ID


def test_journal_is_newest_first(client):
    client.headers.update(auth_headers(ADMIN_ID))
    create(client, serial_number="SN-F")
    create(client, serial_number="SN-G")

    items = client.get("/api/audit").json()["items"]
    assert [i["changes"]["serial_number"]["to"] for i in items] == ["SN-G", "SN-F"]


# --- доступы -------------------------------------------------------------


def test_access_grant_and_revoke_are_logged(client, config, database):
    adduser_command(config, database, ADMIN_ID, "777 Иван Петров", "Админ Админов")
    removeuser_command(config, database, ADMIN_ID, "777", "Админ Админов")

    client.headers.update(auth_headers(ADMIN_ID))
    items = client.get("/api/audit").json()["items"]

    assert [i["action"] for i in items] == ["access_revoke", "access_grant"]
    assert all(i["entity_type"] == "user" and i["entity_id"] == "777" for i in items)
    assert all(i["actor_name"] == "Админ Админов" for i in items)
    assert items[1]["changes"]["name"] == {"from": None, "to": "Иван Петров"}


def test_rejected_bot_command_is_not_logged(client, config, database):
    # Не админ — команда отклонена, следов быть не должно
    adduser_command(config, database, STRANGER_ID, "888", "Чужой")

    client.headers.update(auth_headers(ADMIN_ID))
    assert client.get("/api/audit").json()["total"] == 0


def test_repeated_grant_is_logged_once(client, config, database):
    adduser_command(config, database, ADMIN_ID, "999 Пётр", "Админ")
    adduser_command(config, database, ADMIN_ID, "999 Пётр", "Админ")

    client.headers.update(auth_headers(ADMIN_ID))
    assert client.get("/api/audit").json()["total"] == 1
