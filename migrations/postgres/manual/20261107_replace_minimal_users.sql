-- ONE-OFF users replacement. Prepared for review; not executed by the application.
-- Run with the app stopped:
--   psql -X -v ON_ERROR_STOP=1 -d <target_database> -f <this-file>
--
-- Preconditions: public.users contains ONLY id BIGINT and created_at TIMESTAMPTZ,
-- with an id primary key and ordinary default permissions. Existing IDs survive.
-- Incoming foreign keys are discovered, locked, and recreated with their original
-- names, actions, deferrability, and validation state. Child row values do not change.
-- Unexpected customizations/dependencies abort the transaction rather than disappear.
--
-- IMPORTANT: this replaces ONLY the users portion of migration 012. It does not
-- create folders/evidence/match_evidence or matches.evidence_initialized_at.
-- Do NOT run the unchanged 012 afterward: its ADD COLUMN statements would conflict.
-- This file is not repeatable. COMMIT below makes the replacement visible to the app.

BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
SET LOCAL search_path = pg_catalog, public;

LOCK TABLE public.users IN ACCESS EXCLUSIVE MODE;

DO $$
DECLARE
    user_table CONSTANT oid := 'public.users'::regclass;
BEGIN
    IF to_regclass('public.users_legacy') IS NOT NULL THEN
        RAISE EXCEPTION 'public.users_legacy already exists; inspect it before proceeding.';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
        WHERE oid = user_table AND relkind = 'r' AND NOT relispartition
          AND NOT relrowsecurity AND NOT relforcerowsecurity AND relacl IS NULL
    ) THEN
        RAISE EXCEPTION 'Expected an ordinary users table without RLS or explicit grants.';
    END IF;
    IF (SELECT count(*) FROM pg_attribute
        WHERE attrelid = user_table AND attnum > 0 AND NOT attisdropped) <> 2
       OR NOT EXISTS (
           SELECT 1 FROM pg_attribute WHERE attrelid = user_table AND attname = 'id'
             AND atttypid = 'bigint'::regtype AND attnotnull AND NOT attisdropped
       )
       OR NOT EXISTS (
           SELECT 1 FROM pg_attribute WHERE attrelid = user_table AND attname = 'created_at'
             AND atttypid = 'timestamptz'::regtype AND attnotnull AND NOT attisdropped
       ) THEN
        RAISE EXCEPTION 'Expected only id BIGINT NOT NULL and created_at TIMESTAMPTZ NOT NULL. No expanded data will be discarded.';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c JOIN pg_attribute a
          ON a.attrelid = c.conrelid AND a.attname = 'id'
        WHERE c.conrelid = user_table AND c.contype = 'p'
          AND c.conkey = ARRAY[a.attnum]::smallint[]
    ) OR EXISTS (
        -- PostgreSQL 18 also records ordinary NOT NULL constraints here ('n').
        SELECT 1 FROM pg_constraint WHERE conrelid = user_table AND contype NOT IN ('p', 'n')
    ) OR EXISTS (
        SELECT 1 FROM pg_index WHERE indrelid = user_table AND NOT indisprimary
    ) OR EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgrelid = user_table AND NOT tgisinternal
    ) OR EXISTS (
        SELECT 1 FROM pg_attribute WHERE attrelid = user_table
          AND (attacl IS NOT NULL OR attidentity <> '' OR attgenerated <> '')
    ) THEN
        RAISE EXCEPTION 'Unexpected constraints, indexes, triggers, column grants, or generated/identity columns on users; inspect before replacement.';
    END IF;
END
$$;

-- Capture definitions BEFORE renaming, while REFERENCES users means the intended
-- final table name. Child partitions inherit their parent FK; recreate roots only.
CREATE TEMP TABLE users_replacement_fks ON COMMIT DROP AS
SELECT c.conrelid AS child_oid,
       n.nspname AS child_schema,
       t.relname AS child_table,
       c.conname AS constraint_name,
       pg_get_constraintdef(c.oid) AS definition
FROM pg_constraint c
JOIN pg_class t ON t.oid = c.conrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE c.contype = 'f' AND c.confrelid = 'public.users'::regclass
  AND c.conparentid = 0;

CREATE TEMP TABLE users_replacement_owner ON COMMIT DROP AS
SELECT pg_get_userbyid(relowner) AS role_name,
       obj_description(oid, 'pg_class') AS table_comment
FROM pg_class WHERE oid = 'public.users'::regclass;

DO $$
DECLARE
    child record;
    pk_name name;
BEGIN
    FOR child IN
        SELECT DISTINCT child_oid, child_schema, child_table
        FROM users_replacement_fks ORDER BY child_oid
    LOOP
        EXECUTE format('LOCK TABLE %I.%I IN ACCESS EXCLUSIVE MODE',
                       child.child_schema, child.child_table);
    END LOOP;
    -- Free users_pkey (and its backing index name) for the complete CREATE below.
    SELECT conname INTO STRICT pk_name FROM pg_constraint
    WHERE conrelid = 'public.users'::regclass AND contype = 'p';
    EXECUTE format('ALTER TABLE public.users RENAME CONSTRAINT %I TO users_legacy_pkey',
                   pk_name);
END
$$;

ALTER TABLE public.users RENAME TO users_legacy;

CREATE TABLE public.users (
    id BIGINT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    name TEXT,
    profile_links JSONB NOT NULL DEFAULT '[]'::jsonb,
    first_name TEXT,
    last_name TEXT,
    preferred_name TEXT,
    email TEXT,
    phone TEXT,
    residence_country CHAR(2),
    residence_region TEXT,
    residence_city TEXT,
    timezone TEXT,
    linkedin_url TEXT,
    github_url TEXT,
    portfolio_url TEXT,
    desired_role_titles TEXT[] NOT NULL DEFAULT '{}',
    employment_types TEXT[] NOT NULL DEFAULT '{}',
    work_arrangements TEXT[] NOT NULL DEFAULT '{}',
    preferred_work_locations TEXT[] NOT NULL DEFAULT '{}',
    willing_to_relocate BOOLEAN,
    relocation_destinations TEXT[] NOT NULL DEFAULT '{}',
    willing_to_travel BOOLEAN,
    notice_period_days INTEGER CHECK (notice_period_days >= 0),
    earliest_start_date DATE,
    professional_headline TEXT,
    professional_summary TEXT,
    skills TEXT[] NOT NULL DEFAULT '{}',
    employment_history JSONB NOT NULL DEFAULT '[]'::jsonb,
    education JSONB NOT NULL DEFAULT '[]'::jsonb,
    languages JSONB NOT NULL DEFAULT '[]'::jsonb,
    work_authorizations JSONB NOT NULL DEFAULT '[]'::jsonb,
    compensation_expectations JSONB NOT NULL DEFAULT '[]'::jsonb,
    resumes JSONB NOT NULL DEFAULT '[]'::jsonb,
    default_resume_id UUID,
    CONSTRAINT users_profile_links_array CHECK (jsonb_typeof(profile_links) = 'array'),
    CONSTRAINT users_employment_array CHECK (jsonb_typeof(employment_history) = 'array'),
    CONSTRAINT users_education_array CHECK (jsonb_typeof(education) = 'array'),
    CONSTRAINT users_languages_array CHECK (jsonb_typeof(languages) = 'array'),
    CONSTRAINT users_authorizations_array CHECK (jsonb_typeof(work_authorizations) = 'array'),
    CONSTRAINT users_compensation_array CHECK (jsonb_typeof(compensation_expectations) = 'array'),
    CONSTRAINT users_resumes_array CHECK (jsonb_typeof(resumes) = 'array')
);

INSERT INTO public.users (id, created_at)
SELECT id, created_at FROM public.users_legacy;

DO $$
DECLARE
    fk record;
    original record;
BEGIN
    -- Abort if CREATE applied role-specific default grants that would change access.
    IF EXISTS (SELECT 1 FROM pg_class WHERE oid = 'public.users'::regclass
               AND relacl IS NOT NULL) THEN
        RAISE EXCEPTION 'Default privileges changed access on the new users table; review grants explicitly.';
    END IF;
    IF EXISTS (
        (SELECT id, created_at FROM public.users_legacy
         EXCEPT SELECT id, created_at FROM public.users)
        UNION ALL
        (SELECT id, created_at FROM public.users
         EXCEPT SELECT id, created_at FROM public.users_legacy)
    ) THEN
        RAISE EXCEPTION 'User identity/timestamp copy verification failed.';
    END IF;

    FOR fk IN SELECT * FROM users_replacement_fks ORDER BY child_oid, constraint_name LOOP
        EXECUTE format('ALTER TABLE %I.%I DROP CONSTRAINT %I',
                       fk.child_schema, fk.child_table, fk.constraint_name);
        EXECUTE format('ALTER TABLE %I.%I ADD CONSTRAINT %I %s',
                       fk.child_schema, fk.child_table, fk.constraint_name, fk.definition);
    END LOOP;

    SELECT * INTO STRICT original FROM users_replacement_owner;
    EXECUTE format('COMMENT ON TABLE public.users IS %L', original.table_comment);
    EXECUTE format('ALTER TABLE public.users OWNER TO %I', original.role_name);
END
$$;

-- Views or other uncopied dependencies prevent this drop and roll back the swap.
-- No CASCADE: never silently remove unrelated objects.
DROP TABLE public.users_legacy RESTRICT;

COMMIT;
