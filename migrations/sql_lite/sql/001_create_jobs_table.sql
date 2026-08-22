-- SQLite Schema Configuration
CREATE TABLE ats (
    ats_name TEXT,
    company_slug TEXT,
    company_name TEXT,
    url TEXT,
    tier INT,

    PRIMARY KEY (ats_name, company_slug)
);

CREATE TABLE jobs (
    id TEXT PRIMARY KEY,

    ats_type TEXT NOT NULL,
    ats_id TEXT NOT NULL,

    url TEXT NOT NULL,
    apply_url TEXT,

    title TEXT NOT NULL,
    company_slug TEXT NOT NULL,
    location TEXT,
    country_iso TEXT,
    region TEXT,
    employment_type TEXT DEFAULT 'FULL_TIME',
    description TEXT NOT NULL,

    salary_min REAL,
    salary_max REAL,
    salary_currency TEXT,

    is_normalized INTEGER NOT NULL DEFAULT 0,

    posted_at TEXT, -- SQLite stores datetimes as ISO8601 strings
    fetched_at TEXT NOT NULL,

    FOREIGN KEY (ats_type, company_slug) REFERENCES ats (ats_name, company_slug)
        ON UPDATE CASCADE
        ON DELETE CASCADE
);

-- Native deduplication constraint barrier
CREATE UNIQUE INDEX idx_jobs_ats_composite ON jobs (ats_type, ats_id);
CREATE INDEX idx_jobs_unnormalized ON jobs (id) WHERE is_normalized = 0;