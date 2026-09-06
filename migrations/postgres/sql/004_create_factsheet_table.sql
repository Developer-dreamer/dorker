-- 1. Custom Enum Types for Literals
CREATE TYPE geographic_scope_enum AS ENUM (
    'UNKNOWN',
    'DOMESTIC',
    'REGIONAL',
    'GLOBAL'
);
CREATE TYPE workplace_type_enum AS ENUM (
    'REMOTE',
    'HYBRID',
    'ON_SITE',
    'UNKNOWN'
);
CREATE TYPE job_family AS ENUM (
    'BACKEND',
    'FRONTEND',
    'FULLSTACK',
    'QA_SDET',
    'DEVOPS_PLATFORM',
    'DATA_AI',
    'MOBILE',
    'NON_TECHNICAL',
    'OTHER'
);
CREATE TYPE region_enum AS ENUM (
    'EMEA',
    'LATAM',
    'APAC',
    'AMER',
    'APJ',
    'CEE',
    'MENA',
    'SEA'
    );


CREATE TYPE timezone_overlap AS ENUM (
    'EMEA',
    'US_EAST',
    'US_WEST',
    'APAC'
);

 -- 2. Jobs Fact Sheets Table
CREATE TABLE jobs_fact_sheets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT 'llama3.1',
    version TEXT NOT NULL DEFAULT 'v0.0.0',

    job_family job_family NOT NULL,
     
    -- 1. Location & Legal Constraints
    geographic_scope geographic_scope_enum NOT NULL,
    workplace_type workplace_type_enum NOT NULL,
    target_jurisdiction CHAR(2)
        CHECK (target_jurisdiction ~ '^[A-Z]{2}$' OR target_jurisdiction IS NULL),
    region region_enum,
    office_location_city TEXT,
    timezone_overlap_requested TEXT,
    -- 2. Seniority & Experience
    min_years_experience INTEGER CHECK (min_years_experience >= 0),
    is_experience_flexible BOOLEAN NOT NULL DEFAULT FALSE,
    -- 3. Technology Stack & Architectural Focus
    primary_backend_languages TEXT [] NOT NULL DEFAULT '{}',
    secondary_tools TEXT [] NOT NULL DEFAULT '{}',
    is_legacy_maintenance BOOLEAN NOT NULL DEFAULT FALSE,
    is_pure_network_or_systems BOOLEAN NOT NULL DEFAULT FALSE,
    -- 4. Operational Red Flags
    has_mandatory_travel BOOLEAN NOT NULL DEFAULT FALSE,
    has_uncompensated_oncall BOOLEAN NOT NULL DEFAULT FALSE,
    detected_operational_cues TEXT [] NOT NULL DEFAULT '{}',
    -- Metadata / Audit Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);
-- 3. Helpful Indexes
CREATE INDEX idx_jobs_fact_sheets_job_id ON jobs_fact_sheets (job_id);
CREATE INDEX idx_jobs_fact_sheets_geographic_scope ON jobs_fact_sheets (geographic_scope);
CREATE INDEX idx_jobs_fact_sheets_workplace_type ON jobs_fact_sheets (workplace_type);
-- GIN indexes for array containment queries (e.g., WHERE primary_backend_languages @> ARRAY['Python'])
CREATE INDEX idx_jobs_fact_sheets_languages ON jobs_fact_sheets USING GIN (primary_backend_languages);
CREATE INDEX idx_jobs_fact_sheets_tools ON jobs_fact_sheets USING GIN (secondary_tools);