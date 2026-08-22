CREATE TABLE matches (
    -- Keys
    job_id TEXT NOT NULL,

    -- Core matching constraints
    is_match BOOLEAN NOT NULL, -- false if REJECTED / true if any other
    suitability_tier TEXT NOT NULL, -- SUITABLE / STRECH / RUNWAY / REJECTED
    pipeline_status TEXT NOT NULL DEFAULT 'PENDING', -- PENDING / APPLIED / GHOSTED / INTERVIEWING / REJECTED (Early) / REJECTED (Late) / DECLINED / OFFER
    technical_capability_score REAL NOT NULL, -- score [0, 1] whether candidate suitable by technologies and experience
    strategic_value_score REAL NOT NULL, -- score [0, 1] whether the company has complex challenges and seems perspective for user's growth
    strategic_reason TEXT NOT NULL, -- single sentence explaining why it matches or fails based on constraints

    -- Analytics
    analytics TEXT NOT NULL, -- json containing arrays of pros, cons, warnings

    -- Technical
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
);