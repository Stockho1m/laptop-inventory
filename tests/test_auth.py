"""Проверка подписи initData и белого списка."""

from __future__ import annotations

import time

import pytest

from app.auth import CLOCK_SKEW_SEC, InitDataError, verify_init_data
from tests.conftest import (
    ADMIN_ID,
    ALLOWED_ID,
    OTHER_TOKEN,
    STRANGER_ID,
    TEST_TOKEN,
    auth_headers,
    build_init_data,
    headers_for,
)


# --- подпись -------------------------------------------------------------


def test_valid_init_data_is_accepted():
    user = verify_init_data(build_init_data(user_id=777), TEST_TOKEN, 86400)
    assert user.telegram_id == 777
    assert user.name == "Тест Юзер"


def test_signature_field_is_part_of_hmac():
    """Поле signature входит в data_check_string при проверке по токену бота."""
    init_data = build_init_data(extra={"signature": "Ed25519-signature-from-telegram"})
    user = verify_init_data(init_data, TEST_TOKEN, 86400)
    assert user.telegram_id == ALLOWED_ID


def test_tampered_hash_is_rejected():
    with pytest.raises(InitDataError):
        verify_init_data(build_init_data(tamper_hash=True), TEST_TOKEN, 86400)


def test_modified_payload_is_rejected():
    """Подменить user.id, не зная токена, невозможно."""
    init_data = build_init_data(user_id=ALLOWED_ID)
    forged = init_data.replace(str(ALLOWED_ID), str(ADMIN_ID))
    assert forged != init_data
    with pytest.raises(InitDataError):
        verify_init_data(forged, TEST_TOKEN, 86400)


def test_init_data_signed_with_other_token_is_rejected():
    with pytest.raises(InitDataError):
        verify_init_data(build_init_data(token=OTHER_TOKEN), TEST_TOKEN, 86400)


def test_missing_hash_is_rejected():
    with pytest.raises(InitDataError):
        verify_init_data(build_init_data(include_hash=False), TEST_TOKEN, 86400)


def test_empty_init_data_is_rejected():
    with pytest.raises(InitDataError):
        verify_init_data("", TEST_TOKEN, 86400)


def test_missing_user_is_rejected():
    with pytest.raises(InitDataError):
        verify_init_data(build_init_data(include_user=False), TEST_TOKEN, 86400)


def test_expired_init_data_is_rejected():
    stale = int(time.time()) - 86401
    with pytest.raises(InitDataError):
        verify_init_data(build_init_data(auth_date=stale), TEST_TOKEN, 86400)


def test_init_data_just_inside_max_age_is_accepted():
    fresh_enough = int(time.time()) - 86000
    user = verify_init_data(build_init_data(auth_date=fresh_enough), TEST_TOKEN, 86400)
    assert user.telegram_id == ALLOWED_ID


def test_auth_date_from_far_future_is_rejected():
    future = int(time.time()) + CLOCK_SKEW_SEC + 60
    with pytest.raises(InitDataError, match="Часы сервера отстают"):
        verify_init_data(build_init_data(auth_date=future), TEST_TOKEN, 86400)


# --- белый список через API ---------------------------------------------


def test_api_requires_init_data(client):
    response = client.get("/api/me")
    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


def test_api_rejects_forged_init_data(client):
    response = client.get("/api/me", headers=auth_headers(ALLOWED_ID, tamper_hash=True))
    assert response.status_code == 401


def test_api_rejects_expired_init_data(client):
    stale = int(time.time()) - 90000
    response = client.get("/api/me", headers=auth_headers(ALLOWED_ID, auth_date=stale))
    assert response.status_code == 401


def test_stranger_gets_403_with_own_id(client):
    response = client.get("/api/me", headers=auth_headers(STRANGER_ID))
    assert response.status_code == 403
    body = response.json()
    assert body["error"] == "forbidden"
    assert body["telegram_id"] == STRANGER_ID


def test_admin_has_access_without_whitelist(client):
    response = client.get("/api/me", headers=auth_headers(ADMIN_ID))
    assert response.status_code == 200
    assert response.json() == {
        "telegram_id": ADMIN_ID,
        "name": "Тест Юзер",
        "is_admin": True,
    }


def test_whitelisted_user_has_access(allowed_client):
    response = allowed_client.get("/api/me")
    assert response.status_code == 200
    assert response.json()["is_admin"] is False


def test_every_endpoint_is_protected(client):
    unprotected = []
    requests = [
        ("GET", "/api/me", None),
        ("GET", "/api/meta", None),
        ("GET", "/api/laptops", None),
        ("GET", "/api/laptops/1", None),
        ("POST", "/api/laptops", {"serial_number": "X1"}),
        ("PATCH", "/api/laptops/1", {"model": "X"}),
        ("POST", "/api/qr/parse", {"raw": "X1"}),
    ]
    for method, path, body in requests:
        response = client.request(method, path, json=body)
        if response.status_code != 401:
            unprotected.append((method, path, response.status_code))
    assert unprotected == []


def test_healthz_is_open(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_index_is_always_revalidated(client):
    """Ссылки на css/js версионированы, а сама страница — нет.

    Без no-cache WebView Telegram показывает старую разметку после обновления.
    """
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    assert "<title>Инвентаризация ноутбуков</title>" in response.text


def test_init_data_header_is_not_accepted_from_query_string(client):
    """Единственный источник initData — заголовок, не URL."""
    init_data = build_init_data(user_id=ADMIN_ID)
    response = client.get(f"/api/me?{init_data}")
    assert response.status_code == 401
    assert headers_for(init_data)  # заголовок остаётся рабочим способом
