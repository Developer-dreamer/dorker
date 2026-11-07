# Evidence database preparation

> Migration 012 now also includes the reviewed profile expansion described in
> `profile-backend.md`. This revised SQL has not been executed anywhere. The PostgreSQL
> audit and earlier migration-test results below concern the earlier evidence-only
> revision, not the new profile columns. Current verification is offline/in-memory only.

> Superseded application-storage proposal: the production repository reads and writes
> `materials_snapshot`, mapping it to the Python `materials` field. It ignores the
> trial `applications.materials` column. The cleanup/conversion instructions below
> describe the earlier proposal and are **not approved for execution**. Do not run
> `20261106_cleanup_evidence_trial.sql` as a prerequisite for the working repository.
> Evidence-library integration remains separate until explicitly verified.

Scope: prepare the agreed path-based schema and reconcile the earlier application
evidence trial. These scripts have **not** been applied to the live database.
The evidence API and folder operations are now implemented; see `evidence-backend.md`.
XML import and selector/prompt integration remain later slices.

## Live audit — 2026-11-06

Inspected local `dorker_db` on PostgreSQL 16.13 using read-only sessions. No candidate
text was extracted; data inspection used aggregate counts. The table/constraint dump
was schema-only. Counts below are observations, not assumptions built into the scripts.

| Object | Observed state | Treatment |
| --- | --- | --- |
| `users` | One row; bigint primary key and `created_at` | Preserve identity; add nullable name, profile links, update timestamp |
| `folders`, `evidence`, `match_evidence` | Absent | Create in migration 012 |
| `applications.materials` | UUID array; all 31 arrays empty | Cleanup converts to JSONB using existing snapshot text |
| `applications.materials_snapshot` | JSONB arrays; 29 nonempty out of 31 | Preserve the column and all values as fallback |
| `applications.evidence_initialized` | True on two rows | Archive before dropping; do not infer match initialization from it |
| `applications.evidence_revision` | Zero on all rows | Archive before dropping |
| `applications.user_id` | Two populated values; FK to `users` | Preserve |
| `profiles`, `saved_jobs`, `api_cache` | Empty | Preserve: sibling `stapply` schema/application uses these objects |
| `stapply_applications` | One row | Preserve: sibling project owns this data |

No non-internal triggers, public views/materialized views, or public functions matching
the trial evidence field names were found. This does not prove that no external client
uses the fields. The sibling `stapply` source references its tables, so empty tables are
not treated as disposable. Cleanup uses restrictive drops, not `CASCADE`.

The current repository had a concrete inconsistency: `save_packet` inserted JSON into
`materials_snapshot`, while every application read decoded `materials`. The accompanying
one-line backend correction makes writes use `materials` too. **Do not run that changed
write path against the old UUID-array column.** The cleanup SQL and code correction
must be deployed together while application writers are stopped.

## Scripts and execution order

1. Stop application/API writers and verify a restorable database backup. Take a fresh
   dump into a private location outside the repository if needed. The trial archive is
   useful recovery metadata, not a replacement for a complete backup.
2. On the audited trial database, apply
   `migrations/postgres/manual/20261106_cleanup_evidence_trial.sql`.
3. Apply `migrations/postgres/sql/012_create_evidence_table.sql`.
4. Deploy the repository correction before restarting application writers.

From the repository root, with `PG_DSN` explicitly set to the intended database:

```sh
psql -X --set=ON_ERROR_STOP=1 --dbname="$PG_DSN" \
  --file=migrations/postgres/manual/20261106_cleanup_evidence_trial.sql

psql -X --set=ON_ERROR_STOP=1 --dbname="$PG_DSN" \
  --file=migrations/postgres/sql/012_create_evidence_table.sql
```

Both files contain their own `BEGIN`/`COMMIT`; do not add `--single-transaction`.
Each script is independently atomic. If 012 fails after cleanup commits, cleanup
remains applied; inspect the error before proceeding. Do not blindly rerun cleanup.
For a database built from the original numbered migrations, `materials` is already
JSONB: **skip the trial cleanup** and apply 012 directly.

The cleanup locks `applications`, verifies the expected trial types, and refuses
nonempty UUID selections or non-array JSON snapshots. It creates
`evidence_trial_backup_20261106.application_evidence` containing the old UUID arrays,
ownership and trial flags for every application, keyed by application ID. It then
converts `materials` to JSONB from `materials_snapshot` and drops only the two trial
flags. All snapshot values and application rows survive. The separate backup schema
is intentionally retained; neither script drops it.

`materials_snapshot` remains a fallback copy of pre-cleanup data. New backend writes
go only to `materials`; the fallback is **not synchronized** and is not a backup of
future drafts. Restoring the previous trial layout after new writes requires explicit
reconciliation; do not overwrite current evidence with the old fallback automatically.

## Accepted schema and backend responsibilities

- Reuse existing bigint user identities. Evidence IDs must be UUIDv7, supplied by the
  backend using the project's existing `uuid6.uuid7()` support; PostgreSQL 16 is not
  asked to generate UUIDv7. No identities or profile text are automatically seeded.
- `folders(user_id, path)` stores empty and populated paths. Canonical paths have a
  leading slash, no trailing/repeated slash, no `.`/`..` segments, and no control
  characters. `/` is a virtual root, not a folder row. Folder moves are outside v1.
- `evidence` contains UUID, owner, folder path, title, text, matching checkbox, and
  timestamps. A composite foreign key enforces that the folder belongs to the owner.
  Deleting a folder containing evidence is rejected by PostgreSQL.
- `match_evidence(match_id, evidence_id, checked)` holds the shared discussion and
  generation selection. Deleting evidence removes its links; deleting a match also
  removes its links. Neither action rewrites saved application JSON snapshots.
- `matches.evidence_initialized_at` distinguishes never initialized from initialized
  with nothing checked. Existing matches begin with NULL. No old per-draft flags are
  copied into this marker because they do not establish a match-level selection.
- Parent-folder existence, deletion with subfolders, configured-user ownership of
  match selections, and initialization coordination are handled by the evidence service/repository (selector initialization remains pending).
  Direct SQL can bypass these service rules. Serialize folder create/delete operations
  per user (for example, lock the owning user row during the transaction) so parent
  validation and deletion cannot race. PostgreSQL alone does not enforce the path tree.
- Repository writes explicitly maintain `updated_at`; no trigger is introduced.
  Initialization must save the links and timestamp together. The match-level marker
  and linkage assume the current single-user matching runtime; this is not multiuser
  matching isolation or authentication.

## Failure modes

- Each script waits at most five seconds for locks and has a 60-second statement
  timeout. Lock contention or any SQL error aborts its transaction.
- 012 deliberately fails on existing evidence tables or overlapping new columns;
  it does not hide a conflicting schema behind `IF NOT EXISTS`. It supports either
  the observed bigint `users` table or a database where `users` does not yet exist.
- Cleanup deliberately refuses an unexpected/already-cleaned layout, an existing
  backup schema, or populated trial UUID selections. Inspect and reconcile rather
  than removing its guards. Dependencies on dropped columns also stop the cleanup.
- Converting `applications.materials` can rewrite that table and requires an exclusive
  lock. The observed table has 31 rows, but this should be rechecked before execution.
- The retained sibling tables and `materials_snapshot` are intentional; a clean
  evidence schema does not mean deleting unrelated schemas or fallback data.
- Installing the schema alone does not activate evidence selection. The backend
  still uses XML until the subsequent service/import work is implemented.

## Verification

Validation uses an isolated PostgreSQL 16 instance, a schema-only copy of the audited
database, and synthetic records. The local test copy omits the `semver` extension
declaration because it is unavailable locally; no copied table column or constraint
uses that extension. This is not a full deployment/integration check of the application.

The migration checks cover existing-user preservation, creation without a prior user
table, rollback on a late conflict, rerun rejection, invalid paths, duplicate folders,
missing/foreign-owner folders, UUIDv7 validation, nonempty-folder deletion, duplicate
links, initialization surviving unchecking, and evidence deletion preserving saved
application snapshots. Cleanup checks cover populated-selection refusal, malformed
snapshot refusal, archived metadata, preservation of both JSON columns, and reruns.
The actual application repository is exercised against the temporary database to
verify that save/read returns the same material text and does not change the fallback.
