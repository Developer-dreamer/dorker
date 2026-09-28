-- 4. OpenAI Batch Operations
CREATE TABLE openai_batches
(
    id                 TEXT PRIMARY KEY,
    input_file_id      TEXT    NOT NULL,
    output_file_id     TEXT,
    error_file_id      TEXT,

    status             TEXT    NOT NULL CHECK (
        status IN (
                   'validating', 'failed', 'in_progress', 'finalizing',
                   'completed', 'expired', 'cancelling', 'cancelled'
            )
        ),

    items UUID[] NOT NULL DEFAULT '{}',

    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    downloaded_at      TIMESTAMPTZ
);

CREATE INDEX idx_batch_items_ids ON openai_batches USING gin (items);