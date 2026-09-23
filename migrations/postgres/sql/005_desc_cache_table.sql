CREATE
UNLOGGED TABLE description_cache
(
    key_type   TEXT        NOT NULL,
    key_value  TEXT        NOT NULL,
    payload    BYTEA       NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (key_type, key_value)
);

CREATE INDEX idx_description_cache_updated ON description_cache (updated_at);