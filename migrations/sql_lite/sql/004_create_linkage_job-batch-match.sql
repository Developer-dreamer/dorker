CREATE TABLE openai_batch_items (
    id TEXT PRIMARY KEY,                    -- Unique ID per request line (e.g. 'req_jobId_timestamp')
    batch_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    match_id TEXT,

    -- State of the individual request
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (
        status IN ('PENDING', 'COMPLETED', 'FAILED', 'CANCELLED')
    ),
    http_status_code INTEGER,                      -- 200, 400, 429, 500, etc.
    error_code TEXT,                               -- OpenAI line-item error code
    error_message TEXT,                            -- OpenAI line-item error message
    
    -- Processing Tracking
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    processed_at TEXT,

    FOREIGN KEY (batch_id) REFERENCES openai_batches(id) ON DELETE CASCADE,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
    FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE
);

CREATE INDEX idx_batch_items_batch_id ON openai_batch_items(batch_id);
CREATE INDEX idx_batch_items_job_id ON openai_batch_items(job_id);
CREATE INDEX idx_batch_items_status ON openai_batch_items(status);