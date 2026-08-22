CREATE TABLE openai_batches (
    -- OpenAI Identifiers
    id TEXT PRIMARY KEY,                           -- OpenAI batch ID (e.g., 'batch_abc123')
    endpoint TEXT NOT NULL,                        -- Target endpoint (e.g., '/v1/chat/completions')
    input_file_id TEXT NOT NULL,                   -- Uploaded .jsonl file ID ('file-xyz')
    output_file_id TEXT,                           -- Successful results file ID
    error_file_id TEXT,                            -- Errors file ID (if failures occurred)
    
    -- Status & Lifecycle
    status TEXT NOT NULL CHECK (
        status IN (
            'validating',
            'failed',
            'in_progress',
            'finalizing',
            'completed',
            'expired',
            'cancelling',
            'cancelled'
        )
    ),
    completion_window TEXT NOT NULL DEFAULT '24h', -- Window SLA
    
    -- Request Counters (from request_counts object)
    total_requests INTEGER NOT NULL DEFAULT 0,
    completed_requests INTEGER NOT NULL DEFAULT 0,
    failed_requests INTEGER NOT NULL DEFAULT 0,
    
    -- OpenAI Unix Timestamps
    created_at INTEGER NOT NULL,                   -- Submission timestamp (Unix epoch seconds)
    in_progress_at INTEGER,
    expires_at INTEGER,
    finalizing_at INTEGER,
    completed_at INTEGER,
    failed_at INTEGER,
    cancelled_at INTEGER,
    
    -- Application Tracking & Metadata
    custom_metadata TEXT,                          -- Stored as JSON string (mirrors OpenAI metadata object)
    last_polled_at INTEGER,                        -- Unix epoch seconds of the last status sync
    downloaded_at INTEGER,                         -- Unix epoch seconds when output file was fetched
    processed_at INTEGER,                          -- Unix epoch seconds when results were ingested into DB
    error_message TEXT                             -- Internal or OpenAI top-level error details
);

-- Indices for polling active jobs and filtering completed runs
CREATE INDEX idx_openai_batches_status ON openai_batches(status);
CREATE INDEX idx_openai_batches_created_at ON openai_batches(created_at DESC);

