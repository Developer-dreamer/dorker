
-- 3. Job Matches & Scoring
CREATE TABLE matches (
    id UUID PRIMARY KEY,
    job_id TEXT NOT NULL,

    is_technical BOOLEAN NOT NULL DEFAULT FALSE,
    suitability_tier TEXT CHECK (
        suitability_tier IN ('SUITABLE', 'STRETCH', 'RUNWAY', 'REJECTED')
    ),
    pipeline_status TEXT NOT NULL DEFAULT 'PENDING' CHECK (
        pipeline_status IN (
            'PENDING', 'APPLIED', 'GHOSTED', 'INTERVIEWING', 
            'REJECTED_EARLY', 'REJECTED_LATE', 'DECLINED', 'OFFER'
        )
    ),
    technical_capability_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    strategic_value_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    confidence_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    strategic_reason TEXT,
    rejection_reason TEXT,

    -- Analytics payload
    analytics JSONB,

    version TEXT NOT NULL DEFAULT 'v0.0.0',

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE INDEX idx_matches_job_id ON matches(job_id);
CREATE INDEX idx_matches_pipeline_status ON matches(pipeline_status);
