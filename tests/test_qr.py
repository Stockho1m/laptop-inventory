"""Парсер содержимого QR: четыре формата и сохранение исходной строки."""

from __future__ import annotations

import pytest

from app.qr import QrParseError, parse_qr

JSON_QR = (
    '{"manufacturer": "Dell", "model": "Latitude 5420", "sn": "ABC-123", '
    '"inv": "INV-7", "status": "в работе", "owner": "Иванов И.И."}'
)

KV_QR = (
    "Производитель: Lenovo\n"
    "Модель: ThinkPad T14\n"
    "S/N = LN-77\n"
    "Инв. номер: 4242\n"
    "Кабинет: 305"
)

URL_QR = "https://inv.example.com/assets?sn=URL-555&model=HP%20ProBook&inv=INV-9"

PLAIN_QR = "PLAIN-SERIAL-001"


def test_json_format():
    parsed = parse_qr(JSON_QR)
    assert parsed.format == "json"
    assert parsed.fields == {
        "manufacturer": "Dell",
        "model": "Latitude 5420",
        "serial_number": "ABC-123",
        "inventory_number": "INV-7",
        "status": "in_use",
        "assigned_to": "Иванов И.И.",
    }
    assert parsed.qr_raw == JSON_QR


def test_key_value_format():
    parsed = parse_qr(KV_QR)
    assert parsed.format == "kv"
    assert parsed.fields == {
        "manufacturer": "Lenovo",
        "model": "ThinkPad T14",
        "serial_number": "LN-77",
        "inventory_number": "4242",
        "location": "305",
    }
    assert parsed.qr_raw == KV_QR


def test_key_value_single_line_with_semicolons():
    parsed = parse_qr("sn=SEMI-1;model=Aspire 5|vendor=Acer")
    assert parsed.format == "kv"
    assert parsed.fields == {
        "serial_number": "SEMI-1",
        "model": "Aspire 5",
        "manufacturer": "Acer",
    }


def test_url_with_query_params():
    parsed = parse_qr(URL_QR)
    assert parsed.format == "url"
    assert parsed.fields == {
        "serial_number": "URL-555",
        "model": "HP ProBook",
        "inventory_number": "INV-9",
    }
    assert parsed.qr_raw == URL_QR


def test_url_without_params_uses_last_path_segment():
    parsed = parse_qr("https://inv.example.com/l/PATH-9")
    assert parsed.format == "url"
    assert parsed.fields == {"serial_number": "PATH-9"}


def test_plain_string_is_serial_number():
    parsed = parse_qr(PLAIN_QR)
    assert parsed.format == "plain"
    assert parsed.fields == {"serial_number": PLAIN_QR}
    assert parsed.qr_raw == PLAIN_QR


def test_barcode_payloads_are_plain_serials():
    """Штрих-код с наклейки несёт только серийник — это формат plain."""
    for raw in ("5CG8123ABC", "CND0123XYZ9", "1234567890128", "PF-2K9L4M"):
        parsed = parse_qr(raw)
        assert parsed.format == "plain"
        assert parsed.fields == {"serial_number": raw}
        assert parsed.qr_raw == raw


def test_raw_is_preserved_as_is():
    raw = "  SN-WITH-SPACES  "
    parsed = parse_qr(raw)
    assert parsed.qr_raw == raw
    assert parsed.fields["serial_number"] == "SN-WITH-SPACES"


def test_unknown_json_keys_give_empty_form():
    parsed = parse_qr('{"foo": 1, "bar": {"nested": true}}')
    assert parsed.format == "json"
    assert parsed.fields == {}


def test_unknown_key_value_pairs_fall_back_to_plain():
    parsed = parse_qr("Dell: Latitude")
    assert parsed.format == "plain"
    assert parsed.fields == {"serial_number": "Dell: Latitude"}


def test_too_long_plain_string_is_not_used_as_serial():
    parsed = parse_qr("X" * 300)
    assert parsed.format == "plain"
    assert parsed.fields == {}


def test_status_labels_and_codes_are_recognized():
    assert parse_qr("sn=A1;status=Доступен").fields["status"] == "in_stock"
    assert parse_qr("sn=A5;status=используется").fields["status"] == "in_use"
    assert parse_qr("sn=A2;status=В РЕМОНТЕ").fields["status"] == "repair"
    assert parse_qr("sn=A3;status=retired").fields["status"] == "retired"
    assert "status" not in parse_qr("sn=A4;status=непонятно").fields


def test_old_status_labels_are_still_recognized():
    # Подписи до переименования могут быть напечатаны на старых наклейках.
    assert parse_qr("sn=B1;status=в работе").fields["status"] == "in_use"
    assert parse_qr("sn=B2;status=на складе").fields["status"] == "in_stock"


def test_empty_qr_is_an_error():
    with pytest.raises(QrParseError):
        parse_qr("   ")


# --- эндпоинт ------------------------------------------------------------


def test_parse_endpoint_returns_fields_and_does_not_save(allowed_client):
    response = allowed_client.post("/api/qr/parse", json={"raw": JSON_QR})
    assert response.status_code == 200
    body = response.json()
    assert body["format"] == "json"
    assert body["fields"]["serial_number"] == "ABC-123"
    assert body["qr_raw"] == JSON_QR
    assert body["existing"] is None

    # Запись создаётся только после подтверждения формы.
    assert allowed_client.get("/api/laptops").json()["total"] == 0


def test_parse_endpoint_reports_existing_laptop(allowed_client):
    created = allowed_client.post(
        "/api/laptops", json={"serial_number": "ABC-123", "source": "manual"}
    ).json()

    body = allowed_client.post("/api/qr/parse", json={"raw": JSON_QR}).json()
    assert body["existing"] is not None
    assert body["existing"]["id"] == created["id"]


def test_parse_endpoint_rejects_empty_raw(allowed_client):
    response = allowed_client.post("/api/qr/parse", json={"raw": ""})
    assert response.status_code == 422
