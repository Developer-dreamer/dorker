-- Prepared for review only. Apply AFTER users has the expanded profile columns
-- (via 012 or the manual users replacement), with the application stopped.
-- Existing dates retain their meaning; notice periods never imply availability.
-- Run: psql -X -v ON_ERROR_STOP=1 -d <target_database> -f <this-file>
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
SET LOCAL search_path = pg_catalog, public;

ALTER TABLE public.users ADD COLUMN availability TEXT;

UPDATE public.users
SET availability = 'from_date'
WHERE earliest_start_date IS NOT NULL;

ALTER TABLE public.users ADD CONSTRAINT users_availability_date CHECK (
    (availability IS NULL AND earliest_start_date IS NULL)
    OR (
        availability IS NOT NULL AND (
            (availability = 'immediately' AND earliest_start_date IS NULL)
            OR (availability = 'from_date' AND earliest_start_date IS NOT NULL)
        )
    )
);

COMMIT;
