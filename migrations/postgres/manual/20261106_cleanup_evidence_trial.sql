-- OPTIONAL, ONE-OFF cleanup for the exact trial layout audited on 2026-11-06.
-- NOT part of the numbered migrations. Read docs/evidence-database-migration.md first.
-- Requires the repository INSERT to use applications.materials instead of materials_snapshot.
-- Stop writers and take a database backup before running with ON_ERROR_STOP=1.
-- No CASCADE drops. Trial metadata is retained in a separate schema.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
SET LOCAL search_path = pg_catalog, public;

LOCK TABLE public.applications IN ACCESS EXCLUSIVE MODE;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute WHERE attrelid = 'public.applications'::regclass
        AND attname = 'materials' AND atttypid = 'uuid[]'::regtype AND NOT attisdropped
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_attribute WHERE attrelid = 'public.applications'::regclass
        AND attname = 'materials_snapshot' AND atttypid = 'jsonb'::regtype AND NOT attisdropped
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_attribute WHERE attrelid = 'public.applications'::regclass
        AND attname = 'evidence_initialized' AND atttypid = 'boolean'::regtype AND NOT attisdropped
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_attribute WHERE attrelid = 'public.applications'::regclass
        AND attname = 'evidence_revision' AND atttypid = 'integer'::regtype AND NOT attisdropped
    ) THEN
        RAISE EXCEPTION 'Unexpected applications layout (or cleanup already applied). Inspect before continuing.';
    END IF;
END
$$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.applications WHERE cardinality(materials) > 0) THEN
        RAISE EXCEPTION 'Trial UUID selections now contain data. Reconcile those selections before cleanup.';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.applications
        WHERE jsonb_typeof(materials_snapshot) IS DISTINCT FROM 'array'
    ) THEN
        RAISE EXCEPTION 'An application snapshot is not a JSON array. Inspect before cleanup.';
    END IF;
END
$$;

-- Plain CREATE deliberately refuses to overwrite an earlier backup/run.
CREATE SCHEMA evidence_trial_backup_20261106;
REVOKE ALL ON SCHEMA evidence_trial_backup_20261106 FROM PUBLIC;
CREATE TABLE evidence_trial_backup_20261106.application_evidence AS
SELECT id AS application_id, user_id, materials,
       evidence_initialized, evidence_revision, CURRENT_TIMESTAMP AS archived_at
FROM public.applications;
ALTER TABLE evidence_trial_backup_20261106.application_evidence
    ADD PRIMARY KEY (application_id);

-- Keep materials_snapshot intact as the maintainer's fallback copy.
-- Replace the empty UUID arrays with the existing JSON snapshots, without renaming
-- or dropping the fallback column. The UUID default must be removed before conversion.
ALTER TABLE public.applications ALTER COLUMN materials DROP DEFAULT;
ALTER TABLE public.applications
    ALTER COLUMN materials TYPE JSONB USING materials_snapshot;
ALTER TABLE public.applications
    ALTER COLUMN materials SET DEFAULT '[]'::jsonb,
    DROP COLUMN evidence_initialized RESTRICT,
    DROP COLUMN evidence_revision RESTRICT;

-- Retain users, applications.user_id, materials_snapshot, and all stapply-owned tables.
-- Do not turn old per-draft flags into the new per-match initialization marker:
-- no persisted match-level evidence selection exists to justify that marker.
COMMIT;
