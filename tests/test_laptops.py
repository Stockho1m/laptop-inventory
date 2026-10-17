"""CRUD ноутбуков: валидация, дубликаты серийников, поиск."""

from __future__ import annotations

from tests.conftest import ALLOWED_ID


def create(client, **fields):
    payload = {"serial_number": "SN-1", "source": "manual"}
    payload.update(fields)
    return client.post("/api/laptops", json=payload)


def test_create_manual_laptop(allowed_client):
    response = create(allowed_client, serial_number="SN-100", model="Latitude 5420")
    assert response.status_code == 201

    laptop = response.json()
    assert laptop["serial_number"] == "SN-100"
    assert laptop["status"] == "in_stock"
    assert laptop["status_label"] == "Доступен"
    assert laptop["source"] == "manual"
    assert laptop["qr_raw"] is None
    assert laptop["created_by_tg_id"] == ALLOWED_ID


def test_create_from_qr_keeps_raw(allowed_client):
    response = create(
        allowed_client,
        serial_number="SN-QR",
        source="qr",
        qr_raw='{"sn": "SN-QR"}',
    )
    assert response.status_code == 201
    laptop = response.json()
    assert laptop["source"] == "qr"
    assert laptop["qr_raw"] == '{"sn": "SN-QR"}'


def test_create_from_barcode_keeps_raw(allowed_client):
    response = create(
        allowed_client,
        serial_number="5CG8123ABC",
        source="barcode",
        qr_raw="5CG8123ABC",
    )
    assert response.status_code == 201
    laptop = response.json()
    assert laptop["source"] == "barcode"
    assert laptop["qr_raw"] == "5CG8123ABC"


def test_invalid_source_is_rejected(allowed_client):
    assert create(allowed_client, source="webcam").status_code == 422


def test_manual_source_cannot_carry_qr_raw(allowed_client):
    laptop = create(
        allowed_client, serial_number="SN-M", source="manual", qr_raw="подделка"
    ).json()
    assert laptop["qr_raw"] is None


def test_serial_number_is_required(allowed_client):
    assert allowed_client.post("/api/laptops", json={"source": "manual"}).status_code == 422
    assert create(allowed_client, serial_number="").status_code == 422
    assert create(allowed_client, serial_number="   ").status_code == 422


def test_explicit_nulls_are_accepted_for_optional_fields(allowed_client):
    response = create(
        allowed_client,
        serial_number="SN-NULL",
        manufacturer=None,
        model=None,
        notes=None,
    )
    assert response.status_code == 201
    assert response.json()["model"] is None


def test_explicit_null_serial_on_update_is_422(allowed_client):
    laptop = create(allowed_client, serial_number="SN-UPD-NULL").json()
    response = allowed_client.patch(
        f"/api/laptops/{laptop['id']}", json={"serial_number": None}
    )
    assert response.status_code == 422


def test_unknown_field_is_rejected(allowed_client):
    assert create(allowed_client, role="admin").status_code == 422


def test_invalid_status_is_rejected(allowed_client):
    assert create(allowed_client, status="сломан").status_code == 422


def test_duplicate_serial_returns_existing_card(allowed_client):
    first = create(allowed_client, serial_number="DUP-1", model="Первый").json()

    response = create(allowed_client, serial_number="DUP-1", model="Второй")
    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "duplicate_serial"
    assert body["existing"]["id"] == first["id"]
    assert body["existing"]["model"] == "Первый"

    assert allowed_client.get("/api/laptops").json()["total"] == 1


def test_duplicate_serial_ignores_case_and_spaces(allowed_client):
    create(allowed_client, serial_number="Dup-2").json()

    response = create(allowed_client, serial_number="  dup-2  ")
    assert response.status_code == 409
    assert allowed_client.get("/api/laptops").json()["total"] == 1


def test_get_laptop_by_id(allowed_client):
    created = create(allowed_client, serial_number="SN-GET").json()
    response = allowed_client.get(f"/api/laptops/{created['id']}")
    assert response.status_code == 200
    assert response.json()["serial_number"] == "SN-GET"


def test_missing_laptop_returns_404(allowed_client):
    response = allowed_client.get("/api/laptops/9999")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_update_laptop(allowed_client):
    created = create(allowed_client, serial_number="SN-UPD", model="Старая").json()
    response = allowed_client.patch(
        f"/api/laptops/{created['id']}",
        json={"model": "Новая", "status": "in_use", "assigned_to": "Петров"},
    )
    assert response.status_code == 200
    laptop = response.json()
    assert laptop["model"] == "Новая"
    assert laptop["status_label"] == "Используется"
    assert laptop["assigned_to"] == "Петров"
    assert laptop["serial_number"] == "SN-UPD"


def test_update_keeps_qr_raw_and_source(allowed_client):
    created = create(
        allowed_client, serial_number="SN-KEEP", source="qr", qr_raw="SN-KEEP"
    ).json()
    laptop = allowed_client.patch(
        f"/api/laptops/{created['id']}", json={"location": "Кабинет 1"}
    ).json()
    assert laptop["qr_raw"] == "SN-KEEP"
    assert laptop["source"] == "qr"


def test_update_to_taken_serial_returns_409(allowed_client):
    first = create(allowed_client, serial_number="SN-A").json()
    second = create(allowed_client, serial_number="SN-B").json()

    response = allowed_client.patch(
        f"/api/laptops/{second['id']}", json={"serial_number": "sn-a"}
    )
    assert response.status_code == 409
    assert response.json()["existing"]["id"] == first["id"]


def test_update_with_own_serial_is_allowed(allowed_client):
    created = create(allowed_client, serial_number="SN-SELF").json()
    response = allowed_client.patch(
        f"/api/laptops/{created['id']}",
        json={"serial_number": "SN-SELF", "model": "Та же"},
    )
    assert response.status_code == 200
    assert response.json()["model"] == "Та же"


def test_update_missing_laptop_returns_404(allowed_client):
    assert allowed_client.patch("/api/laptops/4242", json={"model": "X"}).status_code == 404


def test_search_by_serial_model_and_inventory(allowed_client):
    create(
        allowed_client,
        serial_number="FIND-SN-1",
        model="ThinkPad T14",
        inventory_number="INV-0042",
    )
    create(allowed_client, serial_number="OTHER-1", model="MacBook Air")

    def total(query):
        return allowed_client.get("/api/laptops", params={"q": query}).json()["total"]

    assert total("find-sn") == 1
    assert total("thinkpad") == 1
    assert total("INV-0042") == 1
    assert total("0042") == 1
    assert total("macbook") == 1
    assert total("") == 2
    assert total("нет такого") == 0


def test_search_by_employee_ignores_cyrillic_case_and_yo(allowed_client):
    create(allowed_client, serial_number="EMP-1", assigned_to="Пётр Иванов")
    create(allowed_client, serial_number="EMP-2", assigned_to="Анна Петрова")

    def serials(query, by="all"):
        items = allowed_client.get(
            "/api/laptops", params={"q": query, "by": by}
        ).json()["items"]
        return sorted(item["serial_number"] for item in items)

    assert serials("иванов") == ["EMP-1"]
    assert serials("ПЕТР") == ["EMP-1", "EMP-2"]
    assert serials("петр иванов", by="employee") == ["EMP-1"]


def test_search_by_field_limits_columns(allowed_client):
    create(allowed_client, serial_number="IVAN-777", assigned_to="Сидоров")
    create(allowed_client, serial_number="SN-2", assigned_to="Ivanov")

    def serials(query, by):
        items = allowed_client.get(
            "/api/laptops", params={"q": query, "by": by}
        ).json()["items"]
        return sorted(item["serial_number"] for item in items)

    assert serials("ivan", "all") == ["IVAN-777", "SN-2"]
    assert serials("ivan", "serial") == ["IVAN-777"]
    assert serials("ivan", "employee") == ["SN-2"]


def test_unknown_search_mode_is_rejected(allowed_client):
    response = allowed_client.get("/api/laptops", params={"by": "notes"})
    assert response.status_code == 422


def test_filter_by_status_and_counts(allowed_client):
    create(allowed_client, serial_number="U-1", status="in_use", model="ThinkPad")
    create(allowed_client, serial_number="U-2", status="in_use", model="MacBook")
    create(allowed_client, serial_number="S-1", status="in_stock", model="ThinkPad")
    create(allowed_client, serial_number="R-1", status="repair", model="ThinkPad")

    data = allowed_client.get("/api/laptops", params={"status": "in_use"}).json()
    assert data["total"] == 2
    assert {item["status"] for item in data["items"]} == {"in_use"}
    # Счётчики не зависят от фильтра по статусу — по ним рисуются чипы фильтра.
    assert data["status_counts"] == {
        "in_use": 2, "in_stock": 1, "repair": 1, "retired": 0,
    }

    # А поиск счётчики сужает.
    data = allowed_client.get(
        "/api/laptops", params={"q": "thinkpad", "status": "in_use"}
    ).json()
    assert data["total"] == 1
    assert data["status_counts"] == {
        "in_use": 1, "in_stock": 1, "repair": 1, "retired": 0,
    }


def test_invalid_status_filter_is_rejected(allowed_client):
    response = allowed_client.get("/api/laptops", params={"status": "lost"})
    assert response.status_code == 422


def test_sort_by_status_groups_in_fixed_order(allowed_client):
    create(allowed_client, serial_number="RET-1", status="retired")
    create(allowed_client, serial_number="USE-OLD", status="in_use")
    create(allowed_client, serial_number="REP-1", status="repair")
    create(allowed_client, serial_number="STK-1", status="in_stock")
    create(allowed_client, serial_number="USE-NEW", status="in_use")

    items = allowed_client.get("/api/laptops", params={"sort": "status"}).json()["items"]
    assert [item["serial_number"] for item in items] == [
        "USE-NEW", "USE-OLD", "STK-1", "REP-1", "RET-1",
    ]


def test_search_escapes_like_wildcards(allowed_client):
    create(allowed_client, serial_number="PERCENT-1")
    assert allowed_client.get("/api/laptops", params={"q": "%"}).json()["total"] == 0


def test_list_is_sorted_newest_first(allowed_client):
    create(allowed_client, serial_number="OLD-1")
    create(allowed_client, serial_number="NEW-1")
    items = allowed_client.get("/api/laptops").json()["items"]
    assert [item["serial_number"] for item in items] == ["NEW-1", "OLD-1"]


def test_suggestions_are_unique_and_sorted(allowed_client):
    create(allowed_client, serial_number="S-1", manufacturer="Huawei", model="FLMH-X")
    create(allowed_client, serial_number="S-2", manufacturer="Huawei", model="MCLF-X")
    create(allowed_client, serial_number="S-3", manufacturer="Dell", model="FLMH-X")

    data = allowed_client.get("/api/suggestions").json()
    assert data["manufacturers"] == ["Dell", "Huawei"]
    assert data["models"] == ["FLMH-X", "MCLF-X"]


def test_suggestions_include_employees(allowed_client):
    create(allowed_client, serial_number="E-1", assigned_to="Петрова Анна")
    create(allowed_client, serial_number="E-2", assigned_to="Иванов Иван")
    create(allowed_client, serial_number="E-3", assigned_to="Иванов Иван")

    data = allowed_client.get("/api/suggestions").json()
    assert data["employees"] == ["Иванов Иван", "Петрова Анна"]


def test_suggestions_skip_empty_values(allowed_client):
    create(allowed_client, serial_number="S-4", manufacturer="", model=None)

    data = allowed_client.get("/api/suggestions").json()
    assert data["manufacturers"] == []
    assert data["models"] == []


def test_suggestions_require_access(client):
    from tests.conftest import STRANGER_ID, auth_headers

    response = client.get("/api/suggestions", headers=auth_headers(STRANGER_ID))
    assert response.status_code == 403


def test_meta_returns_russian_status_labels(allowed_client):
    statuses = allowed_client.get("/api/meta").json()["statuses"]
    assert {status["value"]: status["label"] for status in statuses} == {
        "in_use": "Используется",
        "in_stock": "Доступен",
        "repair": "В ремонте",
        "retired": "Списан",
    }
