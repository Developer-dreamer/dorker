CREATE TABLE matches (
    -- Keys
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,

    -- Core matching constraints
    is_technical INT NOT NULL DEFAULT 0,
    suitability_tier TEXT CHECK (
        suitability_tier IN ('SUITABLE', 'STRETCH', 'RUNWAY', 'REJECTED')
    ),
    pipeline_status TEXT NOT NULL DEFAULT 'PENDING' CHECK (
        pipeline_status IN (
            'PENDING', 'APPLIED', 'GHOSTED', 'INTERVIEWING', 
            'REJECTED_EARLY', 'REJECTED_LATE', 'DECLINED', 'OFFER'
        )
    ),
    technical_capability_score REAL NOT NULL DEFAULT 0.0, -- score [0, 1] whether candidate suitable by technologies and experience
    strategic_value_score REAL NOT NULL DEFAULT 0.0, -- score [0, 1] whether the company has complex challenges and seems perspective for user's growth
    strategic_reason TEXT, -- single sentence explaining why it matches or fails based on constraints

    -- Analytics
    analytics TEXT, -- json containing arrays of pros, cons, warnings

    -- Technical
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
);