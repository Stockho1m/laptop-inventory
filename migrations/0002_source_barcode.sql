-- Добавляет 'barcode' в допустимые значения laptops.source.
--
-- SQLite не умеет менять CHECK у существующей колонки, поэтому таблица
-- пересоздаётся по штатной процедуре: новая таблица -> копирование данных ->
-- удаление старой -> переименование. Всё внутри одной транзакции: DDL в SQLite
-- транзакционный, так что при сбое база останется в исходном состоянии.
-- Повторный прогон (если процесс упал между COMMIT и записью в
-- schema_migrations) даёт тот же результат.

BEGIN;

CREATE TABLE laptops_new (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    manufacturer     TEXT,
    model            TEXT,
    serial_number    TEXT NOT NULL UNIQUE COLLATE NOCASE,
    inventory_number TEXT,
    status           TEXT NOT NULL DEFAULT 'in_stock'
                     CHECK (status IN ('in_use', 'in_stock', 'repair', 'retired')),
    assigned_to      TEXT,
    location         TEXT,
    notes            TEXT,
    qr_raw           TEXT,
    source           TEXT NOT NULL CHECK (source IN ('qr', 'barcode', 'manual')),
    created_by_tg_id INTEGER NOT NULL,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO laptops_new (
    id, manufacturer, model, serial_number, inventory_number, status,
    assigned_to, location, notes, qr_raw, source, created_by_tg_id,
    created_at, updated_at
)
SELECT
    id, manufacturer, model, serial_number, inventory_number, status,
    assigned_to, location, notes, qr_raw, source, created_by_tg_id,
    created_at, updated_at
FROM laptops;

DROP TABLE laptops;

ALTER TABLE laptops_new RENAME TO laptops;

-- Индексы удалились вместе со старой таблицей, создаём заново.
CREATE INDEX IF NOT EXISTS idx_laptops_model
    ON laptops (model COLLATE NOCASE);

CREATE INDEX IF NOT EXISTS idx_laptops_inventory_number
    ON laptops (inventory_number COLLATE NOCASE);

CREATE INDEX IF NOT EXISTS idx_laptops_created_at
    ON laptops (created_at DESC);

COMMIT;
