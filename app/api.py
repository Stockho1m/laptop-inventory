"""HTTP API Mini App. Каждый эндпоинт требует валидную initData."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from . import repo
from .auth import AuthUser, require_user
from .errors import ApiError
from .models import (
    STATUS_LABELS,
    LaptopCreate,
    LaptopListOut,
    LaptopOut,
    LaptopUpdate,
    MetaOut,
    MeOut,
    QrParseIn,
    QrParseOut,
    SearchBy,
    SortOrder,
    Status,
    SuggestionsOut,
)
from .qr import QrParseError, parse_qr

router = APIRouter(prefix="/api")

CurrentUser = Annotated[AuthUser, Depends(require_user)]


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    with request.app.state.db.session() as conn:
        yield conn


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


def to_out(row: sqlite3.Row) -> LaptopOut:
    data: dict[str, Any] = dict(row)
    data["status_label"] = STATUS_LABELS.get(data["status"], data["status"])
    return LaptopOut(**data)


def _duplicate_error(existing: sqlite3.Row) -> ApiError:
    laptop = to_out(existing)
    return ApiError(
        409,
        "duplicate_serial",
        f"Ноутбук с серийным номером {laptop.serial_number} уже есть в базе.",
        {"existing": laptop.model_dump()},
    )


@router.get("/me", response_model=MeOut)
def get_me(user: CurrentUser) -> MeOut:
    return MeOut(telegram_id=user.telegram_id, name=user.name, is_admin=user.is_admin)


@router.get("/meta", response_model=MetaOut)
def get_meta(user: CurrentUser) -> MetaOut:
    return MetaOut(
        statuses=[
            {"value": value, "label": label} for value, label in STATUS_LABELS.items()
        ]
    )


@router.get("/suggestions", response_model=SuggestionsOut)
def get_suggestions(user: CurrentUser, conn: Conn) -> SuggestionsOut:
    """Производители, модели и сотрудники, уже встречавшиеся в базе."""
    return SuggestionsOut(
        manufacturers=repo.distinct_values(conn, "manufacturer"),
        models=repo.distinct_values(conn, "model"),
        employees=repo.distinct_values(conn, "assigned_to"),
    )


@router.get("/laptops", response_model=LaptopListOut)
def list_laptops(
    user: CurrentUser,
    conn: Conn,
    q: Annotated[str, Query(max_length=100)] = "",
    by: SearchBy = "all",
    status: Status | None = None,
    sort: SortOrder = "new",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> LaptopListOut:
    rows, total, counts = repo.search_laptops(
        conn, q, limit, offset, by=by, status=status, sort=sort
    )
    return LaptopListOut(
        items=[to_out(row) for row in rows], total=total, status_counts=counts
    )


@router.get("/laptops/{laptop_id}", response_model=LaptopOut)
def get_one_laptop(
    user: CurrentUser, conn: Conn, laptop_id: Annotated[int, Path(ge=1)]
) -> LaptopOut:
    row = repo.get_laptop(conn, laptop_id)
    if row is None:
        raise ApiError(404, "not_found", "Ноутбук не найден")
    return to_out(row)


@router.post("/laptops", response_model=LaptopOut, status_code=201)
def create_laptop(
    user: CurrentUser, conn: Conn, payload: LaptopCreate, response: Response
) -> LaptopOut:
    existing = repo.get_laptop_by_serial(conn, payload.serial_number)
    if existing is not None:
        raise _duplicate_error(existing)

    try:
        row = repo.create_laptop(conn, payload.model_dump(), user.telegram_id)
    except repo.DuplicateSerialError:
        # Гонка между проверкой и вставкой — отдаём уже существующую запись.
        existing = repo.get_laptop_by_serial(conn, payload.serial_number)
        if existing is None:
            raise
        raise _duplicate_error(existing) from None

    laptop = to_out(row)
    response.headers["Location"] = f"/api/laptops/{laptop.id}"
    return laptop


@router.patch("/laptops/{laptop_id}", response_model=LaptopOut)
def edit_laptop(
    user: CurrentUser,
    conn: Conn,
    laptop_id: Annotated[int, Path(ge=1)],
    payload: LaptopUpdate,
) -> LaptopOut:
    changes = payload.model_dump(exclude_unset=True)

    serial_number = changes.get("serial_number")
    if serial_number:
        existing = repo.get_laptop_by_serial(conn, serial_number)
        if existing is not None and existing["id"] != laptop_id:
            raise _duplicate_error(existing)

    try:
        row = repo.update_laptop(conn, laptop_id, changes)
    except repo.DuplicateSerialError as exc:
        existing = repo.get_laptop_by_serial(conn, exc.serial_number)
        if existing is None:
            raise
        raise _duplicate_error(existing) from None

    if row is None:
        raise ApiError(404, "not_found", "Ноутбук не найден")
    return to_out(row)


@router.post("/qr/parse", response_model=QrParseOut)
def parse_qr_payload(user: CurrentUser, conn: Conn, payload: QrParseIn) -> QrParseOut:
    """Разбирает содержимое QR и сообщает, есть ли уже такой серийный номер.

    Ничего не сохраняет: запись создаётся только после подтверждения формы.
    """
    try:
        parsed = parse_qr(payload.raw)
    except QrParseError as exc:
        raise ApiError(422, "bad_qr", str(exc)) from None

    existing = None
    serial_number = parsed.fields.get("serial_number")
    if serial_number:
        row = repo.get_laptop_by_serial(conn, serial_number)
        if row is not None:
            existing = to_out(row)

    return QrParseOut(
        format=parsed.format,
        fields=parsed.fields,
        qr_raw=parsed.qr_raw,
        existing=existing,
    )
