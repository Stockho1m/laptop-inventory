from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Iterator
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from app.auth import INIT_DATA_HEADER
from app.config import Config
from app.db import Database
from app.main import create_app

TEST_TOKEN = "123456:TEST-BOT-TOKEN"
OTHER_TOKEN = "123456:OTHER-BOT-TOKEN"

ADMIN_ID = 1000
ALLOWED_ID = 2000
STRANGER_ID = 3000


def build_init_data(
    *,
    token: str = TEST_TOKEN,
    user_id: int = ALLOWED_ID,
    auth_date: int | None = None,
    extra: dict[str, str] | None = None,
    include_user: bool = True,
    include_hash: bool = True,
    tamper_hash: bool = False,
) -> str:
    """Собирает initData и подписывает её так же, как это делает Telegram."""
    fields: dict[str, str] = {
        "auth_date": str(int(time.time() if auth_date is None else auth_date)),
        "query_id": "AAEgTestQueryId",
    }
    if include_user:
        fields["user"] = json.dumps(
            {
                "id": user_id,
                "first_name": "Тест",
                "last_name": "Юзер",
                "username": "tester",
                "language_code": "ru",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    if extra:
        fields.update(extra)

    data_check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    signature = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if tamper_hash:
        signature = ("0" if signature[0] != "0" else "1") + signature[1:]

    payload = dict(fields)
    if include_hash:
        payload["hash"] = signature
    return urlencode(payload)


def headers_for(init_data: str) -> dict[str, str]:
    return {INIT_DATA_HEADER: init_data}


def auth_headers(user_id: int = ALLOWED_ID, **kwargs) -> dict[str, str]:
    return headers_for(build_init_data(user_id=user_id, **kwargs))


@pytest.fixture
def config(tmp_path) -> Config:
    return Config(
        bot_token=TEST_TOKEN,
        admin_ids=frozenset({ADMIN_ID}),
        webapp_url="https://inv.example.com",
        db_path=str(tmp_path / "test.db"),
        bot_enabled=False,
        set_menu_button=False,
    )


@pytest.fixture
def database(config: Config) -> Database:
    database = Database(config.db_path)
    database.migrate()
    return database


@pytest.fixture
def client(config: Config, database: Database) -> Iterator[TestClient]:
    with TestClient(create_app(config, database)) as test_client:
        yield test_client


@pytest.fixture
def allowed_client(client: TestClient, database: Database) -> TestClient:
    """Клиент, чей пользователь внесён в белый список."""
    with database.session() as conn:
        conn.execute(
            "INSERT INTO allowed_users (telegram_id, name, added_by) VALUES (?, ?, ?)",
            (ALLOWED_ID, "Тест Юзер", ADMIN_ID),
        )
    client.headers.update(auth_headers(ALLOWED_ID))
    return client
