-- Path-based evidence library. Run with psql -X -v ON_ERROR_STOP=1 -f <this-file>.
-- Application services/import are a later slice. UUIDv7 values are supplied by Python.
-- One configured user owns matching in the current single-user runtime.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
SET LOCAL search_path = pg_catalog, public;

-- Fail on an unexpected/repeated installation; do not silently accept trial schemas.
DO
$$
    BEGIN
        IF to_regclass('public.folders') IS NOT NULL
            OR to_regclass('public.evidence') IS NOT NULL
            OR to_regclass('public.match_evidence') IS NOT NULL THEN
            RAISE EXCEPTION 'Evidence tables already exist. Inspect them before applying migration 012.';
        END IF;
        IF to_regclass('public.users') IS NOT NULL AND NOT EXISTS (SELECT 1
                                                                   FROM pg_attribute
                                                                   WHERE attrelid = to_regclass('public.users')
                                                                     AND attname = 'id'
                                                                     AND atttypid = 'bigint'::regtype
                                                                     AND NOT attisdropped) THEN
            RAISE EXCEPTION 'Existing users.id must be bigint; preserve existing identities before migrating.';
        END IF;
    END
$$;

-- Reuse the bigint identities already referenced by applications/discussions.
CREATE TABLE IF NOT EXISTS public.users
(
    id                        BIGINT PRIMARY KEY,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    name                      TEXT,
    profile_links             JSONB       NOT NULL DEFAULT '[]'::jsonb,
    first_name                TEXT,
    last_name                 TEXT,
    preferred_name            TEXT,
    email                     TEXT,
    phone                     TEXT,
    residence_country         CHAR(2),
    residence_region          TEXT,
    residence_city            TEXT,
    timezone                  TEXT,
    linkedin_url              TEXT,
    github_url                TEXT,
    portfolio_url             TEXT,
    desired_role_titles       TEXT[]      NOT NULL DEFAULT '{}',
    employment_types          TEXT[]      NOT NULL DEFAULT '{}',
    work_arrangements         TEXT[]      NOT NULL DEFAULT '{}',
    preferred_work_locations  TEXT[]      NOT NULL DEFAULT '{}',
    willing_to_relocate       BOOLEAN,
    relocation_destinations   TEXT[]      NOT NULL DEFAULT '{}',
    willing_to_travel         BOOLEAN,
    notice_period_days        INTEGER CHECK (notice_period_days >= 0),
    earliest_start_date       DATE,
    professional_headline     TEXT,
    professional_summary      TEXT,
    skills                    TEXT[]      NOT NULL DEFAULT '{}',
    employment_history        JSONB       NOT NULL DEFAULT '[]'::jsonb,
    education                 JSONB       NOT NULL DEFAULT '[]'::jsonb,
    languages                 JSONB       NOT NULL DEFAULT '[]'::jsonb,
    work_authorizations       JSONB       NOT NULL DEFAULT '[]'::jsonb,
    compensation_expectations JSONB       NOT NULL DEFAULT '[]'::jsonb,
    resumes                   JSONB       NOT NULL DEFAULT '[]'::jsonb,
    default_resume_id         UUID,
    CONSTRAINT users_profile_links_array CHECK (jsonb_typeof(profile_links) = 'array'),
    CONSTRAINT users_employment_array CHECK (jsonb_typeof(employment_history) = 'array'),
    CONSTRAINT users_education_array CHECK (jsonb_typeof(education) = 'array'),
    CONSTRAINT users_languages_array CHECK (jsonb_typeof(languages) = 'array'),
    CONSTRAINT users_authorizations_array CHECK (jsonb_typeof(work_authorizations) = 'array'),
    CONSTRAINT users_compensation_array CHECK (jsonb_typeof(compensation_expectations) = 'array'),
    CONSTRAINT users_resumes_array CHECK (jsonb_typeof(resumes) = 'array')
);

-- Profile JSONB entries and evidence ownership are validated by the repositories.
-- Linked evidence deletion is blocked until detached from employment/education.
-- No profile is seeded/imported and existing user IDs, names and links are not rewritten.

CREATE TABLE public.folders
(
    user_id    BIGINT      NOT NULL REFERENCES public.users (id) ON DELETE RESTRICT,
    path       TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, path),
    CONSTRAINT folders_canonical_path CHECK (
        path ~ '^/[^/]+(/[^/]+)*$'
            AND path !~ '(^|/)\.{1,2}(/|$)'
            AND path !~ '[[:cntrl:]]'
        )
);
COMMENT ON TABLE public.folders IS
    'Stored paths, including empty folders. Service checks parent existence and rejects deletion with subfolders. No moves in v1.';

CREATE TABLE public.evidence
(
    id               UUID PRIMARY KEY,
    user_id          BIGINT      NOT NULL REFERENCES public.users (id) ON DELETE RESTRICT,
    folder_path      TEXT        NOT NULL,
    title            TEXT        NOT NULL CHECK (length(btrim(title)) > 0),
    text             TEXT        NOT NULL CHECK (length(btrim(text)) > 0),
    use_for_matching BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT evidence_uuid7 CHECK (
        substring(id::text FROM 15 FOR 1) = '7'
            AND substring(id::text FROM 20 FOR 1) IN ('8', '9', 'a', 'b')
        ),
    CONSTRAINT evidence_folder_owner_fk FOREIGN KEY (user_id, folder_path)
        REFERENCES public.folders (user_id, path) ON DELETE RESTRICT
);
CREATE INDEX evidence_folder_idx ON public.evidence (user_id, folder_path);
CREATE INDEX evidence_matching_idx ON public.evidence (user_id) WHERE use_for_matching;

ALTER TABLE public.matches
    ADD COLUMN evidence_initialized_at TIMESTAMPTZ;
COMMENT ON COLUMN public.matches.evidence_initialized_at IS
    'NULL until baseline selection is committed. Non-NULL remains initialized even with no checked evidence. Single-user matching only.';

CREATE TABLE public.match_evidence
(
    match_id    UUID        NOT NULL REFERENCES public.matches (id) ON DELETE CASCADE,
    evidence_id UUID        NOT NULL REFERENCES public.evidence (id) ON DELETE CASCADE,
    checked     BOOLEAN     NOT NULL DEFAULT FALSE,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (match_id, evidence_id)
);
CREATE INDEX match_evidence_evidence_idx ON public.match_evidence (evidence_id);
COMMENT ON TABLE public.match_evidence IS
    'Shared discussion/generation selection. Service validates configured-user ownership; unchecked and absent links are excluded.';

-- updated_at is maintained explicitly by future repository writes, not by triggers.
-- Existing applications, their snapshots, and discussion history are not rewritten.
COMMIT;
