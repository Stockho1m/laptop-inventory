-- Журнал действий: кто, когда и что изменил.
--
-- Таблица только дополняется: записи не редактируются и не удаляются, поэтому
-- история правки не теряется даже после смены значения обратно. Имя автора
-- сохраняется снимком на момент действия — в Telegram его можно поменять.

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_tg_id INTEGER NOT NULL,
    actor_name  TEXT,
    action      TEXT NOT NULL CHECK (action IN (
                    'laptop_create', 'laptop_update',
                    'access_grant', 'access_revoke')),
    entity_type TEXT NOT NULL CHECK (entity_type IN ('laptop', 'user')),
    -- id ноутбука или telegram_id пользователя, поэтому TEXT
    entity_id   TEXT NOT NULL,
    -- JSON вида {"поле": {"from": ..., "to": ...}}; для создания from = null
    changes     TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- История одной записи в карточке
CREATE INDEX IF NOT EXISTS idx_audit_entity
    ON audit_log (entity_type, entity_id, id DESC);

-- Общий журнал, свежие сверху
CREATE INDEX IF NOT EXISTS idx_audit_id_desc
    ON audit_log (id DESC);
