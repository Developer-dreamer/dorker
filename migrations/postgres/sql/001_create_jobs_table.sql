-- 1. ATS Tenants
CREATE TABLE companies (
    id INT PRIMARY KEY,
    ats TEXT NOT NULL,
    slug TEXT NOT NULL,
    name TEXT,
    url TEXT,
    tier INTEGER,
    
    -- Status & Toggles
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    
    -- Execution Tracing
    last_attempt_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    last_scrape_duration_ms INTEGER,
    
    -- Error Tracking
    consecutive_errors INTEGER NOT NULL DEFAULT 0,
    last_error_message TEXT,
    
    -- Yield Tracking
    last_job_count INTEGER NOT NULL DEFAULT 0,
    consecutive_zero_jobs INTEGER NOT NULL DEFAULT 0,
);

-- 2. Scraped Jobs
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,

    ats_type TEXT NOT NULL,
    ats_id TEXT NOT NULL,

    url TEXT NOT NULL,
    apply_url TEXT,

    title TEXT NOT NULL,
    company_id INT NOT NULL,
    location TEXT,
    country_iso VARCHAR(2),
    region TEXT,
    employment_type TEXT DEFAULT 'FULL_TIME',
    description TEXT NOT NULL,

    salary_min DOUBLE PRECISION,
    salary_max DOUBLE PRECISION,
    salary_currency VARCHAR(3),

    is_normalized BOOLEAN NOT NULL DEFAULT FALSE,

    ADD COLUMN searchable tsvector 
    GENERATED ALWAYS AS (
        setweight(to_tsvector('simple', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('simple', coalesce(location, '')), 'B') ||
        setweight(to_tsvector('simple', coalesce(description, '')), 'C')
    ) STORED,

    posted_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMPTZ,

    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE INDEX idx_jobs_unnormalized ON jobs (id) WHERE is_normalized = FALSE;
CREATE INDEX idx_jobs_searchable ON jobs USING GIN (searchable);

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Index title and description with trigrams
CREATE INDEX idx_jobs_title_trgm ON jobs USING GIN (title gin_trgm_ops);
CREATE INDEX idx_jobs_desc_trgm ON jobs USING GIN (description gin_trgm_ops);
