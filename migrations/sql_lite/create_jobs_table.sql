-- SQLite Schema Configuration
CREATE TABLE jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ats_type TEXT NOT NULL,
    ats_id TEXT NOT NULL,
    url TEXT NOT NULL,
    apply_url TEXT,
    title TEXT NOT NULL,
    company_slug TEXT NOT NULL,
    location TEXT,
    is_remote INTEGER DEFAULT 0 NOT NULL, -- SQLite handles booleans as integers (0 or 1)
    employment_type TEXT DEFAULT 'FULL_TIME',
    description TEXT NOT NULL,
    salary_min REAL,
    salary_max REAL,
    salary_currency TEXT,
    application_questions TEXT, -- Stored as string-serialized JSON text in SQLite
    posted_at TEXT, -- SQLite stores datetimes as ISO8601 strings
    fetched_at TEXT NOT NULL
);

-- Native deduplication constraint barrier
CREATE UNIQUE INDEX idx_jobs_ats_composite ON jobs (ats_type, ats_id);

-- Speed optimization index for User-Triggered Pull lookups
CREATE INDEX idx_jobs_search_routing ON jobs (is_remote, employment_type);