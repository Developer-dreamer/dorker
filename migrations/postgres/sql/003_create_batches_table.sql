-- 4. OpenAI Batch Operations
CREATE TABLE openai_batches (
    id TEXT PRIMARY KEY,
    endpoint TEXT NOT NULL,
    input_file_id TEXT NOT NULL,
    output_file_id TEXT,
    error_file_id TEXT,
    
    purpose TEXT NOT NULL CHECK (
        purpose IN ('REJECT', 'RANK', 'GENERATE')
    ),
    
    status TEXT NOT NULL CHECK (
        status IN (
            'validating', 'failed', 'in_progress', 'finalizing',
            'completed', 'expired', 'cancelling', 'cancelled'
        )
    ),
    completion_window TEXT NOT NULL DEFAULT '24h',
    
    total_requests INTEGER NOT NULL DEFAULT 0,
    completed_requests INTEGER NOT NULL DEFAULT 0,
    failed_requests INTEGER NOT NULL DEFAULT 0,
    
    -- OpenAI Unix Epoch Timestamps
    created_at BIGINT NOT NULL,
    in_progress_at BIGINT,
    expires_at BIGINT,
    finalizing_at BIGINT,
    completed_at BIGINT,
    failed_at BIGINT,
    cancelled_at BIGINT,
    
    custom_metadata JSONB,
    last_polled_at BIGINT,
    downloaded_at BIGINT,
    processed_at BIGINT,
    error_message TEXT
);

CREATE INDEX idx_openai_batches_status ON openai_batches(status);
CREATE INDEX idx_openai_batches_created_at ON openai_batches(created_at DESC);

-- 5. OpenAI Batch Line Items
CREATE TABLE openai_batch_items (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    match_id TEXT,

    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (
        status IN ('PENDING', 'COMPLETED', 'FAILED', 'CANCELLED')
    ),
    http_status_code INTEGER,
    error_code TEXT,
    error_message TEXT,
    
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMPTZ,

    FOREIGN KEY (batch_id) REFERENCES openai_batches(id) ON DELETE CASCADE,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
    FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE
);

CREATE INDEX idx_batch_items_batch_id ON openai_batch_items(batch_id);
CREATE INDEX idx_batch_items_job_id ON openai_batch_items(job_id);
CREATE INDEX idx_batch_items_status ON openai_batch_items(status);