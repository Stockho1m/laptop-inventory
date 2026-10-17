"""Парсер содержимого QR-кода.

Формат заранее неизвестен, поэтому поддерживаются четыре варианта:

1. json   — {"model": "Latitude 5420", "sn": "ABC123"}
2. kv     — пары ключ=значение или ключ: значение, разделённые
            переводом строки, ';' или '|'
3. url    — https://host/path?sn=ABC123&model=X либо https://host/l/ABC123
4. plain  — любая другая строка, считается серийным номером

Исходная строка никогда не теряется: вызывающий код пишет её в laptops.qr_raw.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, unquote, urlparse

from .models import MAX_LINE_LEN, MAX_NOTES_LEN, MAX_SERIAL_LEN, STATUS_LABELS

# Синонимы ключей -> поля модели. Ключи сравниваются после нормализации
# (нижний регистр, без пробелов, дефисов, подчёркиваний, слэшей и точек).
FIELD_SYNONYMS: dict[str, tuple[str, ...]] = {
    "manufacturer": (
        "manufacturer", "vendor", "brand", "make", "maker", "mfr",
        "производитель", "изготовитель", "бренд", "вендор", "марка",
    ),
    "model": (
        "model", "modelname", "modelno", "product", "productname",
        "модель", "модельноутбука",
    ),
    "serial_number": (
        "serial", "serialnumber", "serialno", "serialnum", "sn", "sno", "psn",
        "серийныйномер", "серийник", "серийный", "сн",
    ),
    "inventory_number": (
        "inventory", "inventorynumber", "inventoryno", "inventoryid",
        "inv", "invno", "invnumber", "asset", "assettag", "assetnumber",
        "инвентарныйномер", "инвентарный", "инвномер", "инв", "инвентарник",
    ),
    "status": ("status", "state", "статус", "состояние"),
    "assigned_to": (
        "assignedto", "assigned", "user", "username", "owner", "employee", "holder",
        "сотрудник", "пользователь", "владелец", "ответственный", "закрепленза",
    ),
    "location": (
        "location", "place", "room", "office", "site",
        "кабинет", "офис", "место", "местоположение", "расположение", "локация",
    ),
    "notes": (
        "notes", "note", "comment", "comments", "description", "desc",
        "примечание", "примечания", "заметка", "комментарий", "описание",
    ),
}

KEY_TO_FIELD: dict[str, str] = {
    synonym: field
    for field, synonyms in FIELD_SYNONYMS.items()
    for synonym in synonyms
}

MAX_LENGTHS: dict[str, int] = {
    "serial_number": MAX_SERIAL_LEN,
    "notes": MAX_NOTES_LEN,
}

URL_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://\S+$", re.IGNORECASE)
KV_SPLIT_RE = re.compile(r"[\r\n;|]+")
NORMALIZE_RE = re.compile(r"[^0-9a-zа-я]+")

# Нормализованная русская подпись статуса -> код. Старые подписи («в работе»,
# «на складе») остаются синонимами: они могут быть напечатаны на наклейках.
STATUS_BY_LABEL: dict[str, str] = {
    "вработе": "in_use",
    "наскладе": "in_stock",
    **{
        NORMALIZE_RE.sub("", label.lower()): code
        for code, label in STATUS_LABELS.items()
    },
}


class QrParseError(ValueError):
    """Содержимое QR нельзя разобрать (например, пустая строка)."""


@dataclass(frozen=True)
class ParsedQr:
    format: str
    fields: dict[str, str]
    qr_raw: str


def normalize_key(key: str) -> str:
    return NORMALIZE_RE.sub("", key.strip().lower().replace("ё", "е"))


def _clean_value(value: Any) -> str:
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, (int, float)):
        value = str(value)
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _normalize_status(value: str) -> str | None:
    normalized = NORMALIZE_RE.sub("", value.lower().replace("ё", "е"))
    if value.strip() in STATUS_LABELS:
        return value.strip()
    return STATUS_BY_LABEL.get(normalized)


def _put(fields: dict[str, str], field: str, raw_value: Any) -> None:
    """Записывает поле, если оно ещё не заполнено и значение осмысленное."""
    if field in fields:
        return
    value = _clean_value(raw_value)
    if not value:
        return

    if field == "status":
        code = _normalize_status(value)
        if code:
            fields[field] = code
        return

    limit = MAX_LENGTHS.get(field, MAX_LINE_LEN)
    if field == "serial_number" and len(value) > limit:
        # Слишком длинная строка — не серийный номер; пусть пользователь впишет сам.
        return
    fields[field] = value[:limit]


def _from_mapping(items: list[tuple[str, Any]]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for key, value in items:
        field = KEY_TO_FIELD.get(normalize_key(key))
        if field:
            _put(fields, field, value)
    return fields


def _try_json(raw: str) -> dict[str, str] | None:
    if not raw.startswith("{"):
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    items = [
        (str(key), value)
        for key, value in data.items()
        if not isinstance(value, (dict, list))
    ]
    return _from_mapping(items)


def _try_url(raw: str) -> dict[str, str] | None:
    if not URL_RE.match(raw):
        return None
    parsed = urlparse(raw)
    fields = _from_mapping(parse_qsl(parsed.query, keep_blank_values=False))
    if fields:
        return fields

    # Query-параметров нет или они незнакомые: пробуем последний сегмент пути,
    # это частый формат инвентарных ссылок вида https://host/l/SN12345
    segments = [segment for segment in parsed.path.split("/") if segment]
    if segments:
        _put(fields, "serial_number", unquote(segments[-1]))
    return fields


def _try_kv(raw: str) -> dict[str, str] | None:
    items: list[tuple[str, Any]] = []
    for chunk in KV_SPLIT_RE.split(raw):
        chunk = chunk.strip()
        if not chunk:
            continue
        positions = [chunk.find(sep) for sep in ("=", ":")]
        positions = [position for position in positions if position > 0]
        if not positions:
            continue
        cut = min(positions)
        items.append((chunk[:cut], chunk[cut + 1 :].strip()))

    if not items:
        return None
    fields = _from_mapping(items)
    # Формат считаем распознанным только если хотя бы один ключ знакомый,
    # иначе строку честнее отдать как серийный номер.
    return fields or None


def parse_qr(raw: str) -> ParsedQr:
    """Разбирает содержимое QR. Исходная строка возвращается в qr_raw без изменений."""
    if raw is None or not raw.strip():
        raise QrParseError("QR-код пустой")

    stripped = raw.strip()

    # json и url распознаются по форме, поэтому принимаются даже с пустым
    # набором полей (лучше пустая форма, чем JSON-строка в серийном номере).
    for fmt, parser, accept_empty in (
        ("json", _try_json, True),
        ("url", _try_url, True),
        ("kv", _try_kv, False),
    ):
        fields = parser(stripped)
        if fields or (fields is not None and accept_empty):
            return ParsedQr(format=fmt, fields=fields, qr_raw=raw)

    plain: dict[str, str] = {}
    _put(plain, "serial_number", stripped.strip("\"'"))
    return ParsedQr(format="plain", fields=plain, qr_raw=raw)
