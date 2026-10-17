-- Миграции применяются по возрастанию имени файла и ровно один раз
-- (учёт в таблице schema_migrations). Писать их нужно идемпотентно
-- (IF NOT EXISTS), чтобы повторный прогон после сбоя был безопасен.

CREATE TABLE IF NOT EXISTS allowed_users (
    telegram_id INTEGER PRIMARY KEY,
    name        TEXT,
    added_by    INTEGER,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS laptops (
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
    source           TEXT NOT NULL CHECK (source IN ('qr', 'manual')),
    created_by_tg_id INTEGER NOT NULL,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_laptops_model
    ON laptops (model COLLATE NOCASE);

CREATE INDEX IF NOT EXISTS idx_laptops_inventory_number
    ON laptops (inventory_number COLLATE NOCASE);

CREATE INDEX IF NOT EXISTS idx_laptops_created_at
    ON laptops (created_at DESC);
