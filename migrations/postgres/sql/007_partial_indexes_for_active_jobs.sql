-- Migration 007: Partial indexes on active jobs and tenant deletion reconciliation index
-- This dramatically cuts GIN index sizes in PostgreSQL by excluding deleted jobs,
-- while speeding up company-scoped deletion queries.
--
-- NOTE: In a database with millions of rows, running CREATE INDEX without CONCURRENTLY
-- acquires an exclusive table lock that blocks all incoming writes/scrapers!
-- Run each statement individually in autocommit mode (outside a BEGIN...COMMIT transaction).

-- 1. Index for fast tenant job reconciliation & foreign key lookups
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_jobs_company_active ON jobs (company_id) WHERE deleted_at IS NULL;

-- 2. Drop existing full GIN indexes and recreate as partial indexes on active jobs concurrently
DROP INDEX CONCURRENTLY IF EXISTS idx_jobs_searchable;
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_jobs_searchable ON jobs USING GIN (searchable) WHERE deleted_at IS NULL;

DROP INDEX CONCURRENTLY IF EXISTS idx_jobs_desc_trgm;
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_jobs_desc_trgm ON jobs USING GIN (description gin_trgm_ops) WHERE deleted_at IS NULL;

DROP INDEX CONCURRENTLY IF EXISTS idx_jobs_title_trgm;
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_jobs_title_trgm ON jobs USING GIN (title gin_trgm_ops) WHERE deleted_at IS NULL;

DROP INDEX CONCURRENTLY IF EXISTS idx_jobs_location_trgm;
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_jobs_location_trgm ON jobs USING GIN (location gin_trgm_ops) WHERE deleted_at IS NULL;
