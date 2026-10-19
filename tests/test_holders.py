"""Прошлые владельцы ноутбука, выведенные из журнала."""

from __future__ import annotations

from tests.conftest import ALLOWED_ID


def create(client, **fields):
    payload = {"serial_number": "SN-H", "source": "manual"}
    payload.update(fields)
    response = client.post("/api/laptops", json=payload)
    assert response.status_code == 201
    return response.json()


def holders(client, laptop_id):
    response = client.get(f"/api/laptops/{laptop_id}/history")
    assert response.status_code == 200
    return response.json()["holders"]


def test_never_assigned_has_no_holders(allowed_client):
    laptop = create(allowed_client, serial_number="SN-H1")
    assert holders(allowed_client, laptop["id"]) == []


def test_first_owner_is_not_a_past_holder(allowed_client):
    laptop = create(allowed_client, serial_number="SN-H2", assigned_to="Иванов")
    # Иванов владеет прямо сейчас — в прошлых его быть не должно
    assert holders(allowed_client, laptop["id"]) == []


def test_previous_owners_listed_newest_first(allowed_client):
    laptop = create(allowed_client, serial_number="SN-H3", assigned_to="Иванов")
    allowed_client.patch(f"/api/laptops/{laptop['id']}", json={"assigned_to": "Петров"})
    allowed_client.patch(f"/api/laptops/{laptop['id']}", json={"assigned_to": "Сидоров"})

    past = holders(allowed_client, laptop["id"])
    assert [h["name"] for h in past] == ["Петров", "Иванов"]

    # У Иванова известно и начало владения: ноутбук создан уже на него
    assert past[1]["since"] is not None
    assert past[1]["until"] == past[0]["since"]


def test_unassigned_owner_becomes_past_holder(allowed_client):
    laptop = create(allowed_client, serial_number="SN-H4", assigned_to="Иванов")
    allowed_client.patch(f"/api/laptops/{laptop['id']}", json={"assigned_to": None})

    past = holders(allowed_client, laptop["id"])
    assert [h["name"] for h in past] == ["Иванов"]


def test_returning_owner_appears_twice(allowed_client):
    laptop = create(allowed_client, serial_number="SN-H5", assigned_to="Иванов")
    allowed_client.patch(f"/api/laptops/{laptop['id']}", json={"assigned_to": "Петров"})
    allowed_client.patch(f"/api/laptops/{laptop['id']}", json={"assigned_to": "Иванов"})
    allowed_client.patch(f"/api/laptops/{laptop['id']}", json={"assigned_to": "Петров"})

    # Два разных периода владения у каждого — это не дубликат, а история
    assert [h["name"] for h in holders(allowed_client, laptop["id"])] == [
        "Иванов",
        "Петров",
        "Иванов",
    ]


def test_owner_from_before_the_journal_has_unknown_start(allowed_client, database):
    """Ноутбук, заведённый до появления журнала, владельца всё равно покажет."""
    with database.session() as conn:
        conn.execute(
            "INSERT INTO laptops (serial_number, assigned_to, source, created_by_tg_id)"
            " VALUES (?, ?, ?, ?)",
            ("SN-OLD-H", "Старый Владелец", "manual", ALLOWED_ID),
        )
        laptop_id = conn.execute(
            "SELECT id FROM laptops WHERE serial_number = 'SN-OLD-H'"
        ).fetchone()["id"]

    allowed_client.patch(f"/api/laptops/{laptop_id}", json={"assigned_to": "Новый"})

    past = holders(allowed_client, laptop_id)
    assert len(past) == 1
    assert past[0]["name"] == "Старый Владелец"
    # Когда он получил ноутбук, неизвестно — журнала тогда не было
    assert past[0]["since"] is None
    assert past[0]["until"]


def test_other_edits_do_not_touch_holders(allowed_client):
    laptop = create(allowed_client, serial_number="SN-H6", assigned_to="Иванов")
    allowed_client.patch(f"/api/laptops/{laptop['id']}", json={"status": "repair"})
    allowed_client.patch(f"/api/laptops/{laptop['id']}", json={"location": "Склад"})

    assert holders(allowed_client, laptop["id"]) == []
