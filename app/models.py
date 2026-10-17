"""Схемы запросов и ответов API."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

Status = Literal["in_use", "in_stock", "repair", "retired"]

# Коды статусов хранятся в базе и не меняются, подписи можно переименовывать.
STATUS_LABELS: dict[str, str] = {
    "in_use": "Используется",
    "in_stock": "Доступен",
    "repair": "В ремонте",
    "retired": "Списан",
}

# all — серийник, сотрудник, модель и инв. номер; serial / employee — одно поле.
SearchBy = Literal["all", "serial", "employee"]
# new — сначала новые; status — группами по статусу, внутри группы новые сверху.
SortOrder = Literal["new", "status"]

# qr / barcode — отсканировано камерой, исходное содержимое кода лежит в qr_raw.
Source = Literal["qr", "barcode", "manual"]

MAX_SERIAL_LEN = 100
MAX_LINE_LEN = 200
MAX_NOTES_LEN = 2000
MAX_QR_RAW_LEN = 4096


def _line_or_none(value: Any) -> Any:
    """Однострочное поле: схлопываем пробелы, пустое -> None."""
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = " ".join(value.split())
        return cleaned or None
    return value


def _text_or_none(value: Any) -> Any:
    """Многострочное поле: сохраняем переводы строк, пустое -> None."""
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return value


def _required_line(value: Any) -> Any:
    if isinstance(value, str):
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("поле обязательно")
        return cleaned
    if value is None:
        raise ValueError("поле обязательно")
    return value


# Ограничение длины вешается на строковую ветку union, а не на весь тип:
# иначе pydantic попытается применить max_length к None и упадёт с TypeError.
OptLine = Annotated[
    Annotated[str, Field(max_length=MAX_LINE_LEN)] | None,
    BeforeValidator(_line_or_none),
]
OptText = Annotated[
    Annotated[str, Field(max_length=MAX_NOTES_LEN)] | None,
    BeforeValidator(_text_or_none),
]
OptQrRaw = Annotated[
    Annotated[str, Field(max_length=MAX_QR_RAW_LEN)] | None,
    BeforeValidator(_text_or_none),
]
SerialNumber = Annotated[
    str, BeforeValidator(_required_line), Field(min_length=1, max_length=MAX_SERIAL_LEN)
]


class LaptopCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manufacturer: OptLine = None
    model: OptLine = None
    serial_number: SerialNumber
    inventory_number: OptLine = None
    status: Status = "in_stock"
    assigned_to: OptLine = None
    location: OptLine = None
    notes: OptText = None
    qr_raw: OptQrRaw = None
    source: Source = "manual"

    @model_validator(mode="after")
    def _drop_qr_raw_for_manual(self) -> "LaptopCreate":
        if self.source == "manual" and self.qr_raw is not None:
            self.qr_raw = None
        return self


class LaptopUpdate(BaseModel):
    """Частичное обновление: применяются только переданные поля.

    qr_raw и source не редактируются — это данные о происхождении записи.
    """

    model_config = ConfigDict(extra="forbid")

    manufacturer: OptLine = None
    model: OptLine = None
    serial_number: SerialNumber | None = None
    inventory_number: OptLine = None
    status: Status | None = None
    assigned_to: OptLine = None
    location: OptLine = None
    notes: OptText = None

    @model_validator(mode="after")
    def _reject_explicit_null_serial(self) -> "LaptopUpdate":
        # Поле можно не передавать, но присланный явный null — ошибка клиента,
        # а не команда «стереть серийный номер»: он обязателен.
        if "serial_number" in self.model_fields_set and self.serial_number is None:
            raise ValueError("serial_number: поле обязательно")
        return self


class LaptopOut(BaseModel):
    id: int
    manufacturer: str | None
    model: str | None
    serial_number: str
    inventory_number: str | None
    status: str
    status_label: str
    assigned_to: str | None
    location: str | None
    notes: str | None
    qr_raw: str | None
    source: str
    created_by_tg_id: int
    created_at: str
    updated_at: str


class LaptopListOut(BaseModel):
    items: list[LaptopOut]
    total: int
    # Сколько ноутбуков каждого статуса подходит под поиск (без фильтра по статусу).
    status_counts: dict[str, int]


class QrParseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw: Annotated[str, Field(min_length=1, max_length=MAX_QR_RAW_LEN)]


class QrParseOut(BaseModel):
    format: str
    fields: dict[str, str]
    qr_raw: str
    existing: LaptopOut | None = None


class MeOut(BaseModel):
    telegram_id: int
    name: str
    is_admin: bool


class StatusOut(BaseModel):
    value: str
    label: str


class MetaOut(BaseModel):
    statuses: list[StatusOut]


class SuggestionsOut(BaseModel):
    """Уже встречавшиеся значения — подсказки в форме, чтобы не печатать заново."""

    manufacturers: list[str]
    models: list[str]
    employees: list[str]
