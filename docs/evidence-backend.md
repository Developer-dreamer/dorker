# Evidence backend

This slice implements persisted profile, folder, evidence, and per-match selection
management. It uses the schema in migration 012. No migration is executed on startup.
The working application repository continues using `materials_snapshot`; the proposed
trial cleanup/conversion is not required or approved. Evidence-library integration
remains separate from production application storage. This slice was verified with offline tests and an
isolated test PostgreSQL database, not `dorker_db`.

## HTTP contract

All routes are under `/api`. Identity comes from the analyst's configured local owner
(`TELEGRAM_CHAT_ID`), never request bodies. The first profile/library mutation creates
that user row if absent; pre-existing users and their IDs are preserved.

| Method | Route | Behavior |
| --- | --- | --- |
| GET / PATCH | `/profile` | Dedicated profile service; see `profile-backend.md` |
| GET | `/folders` | List the owner's folder paths |
| POST | `/folders` | Create `{"path":"/cv/experience"}`; parent must exist |
| DELETE | `/folders?path=/cv/experience` | Delete an empty folder only |
| GET | `/evidence` | List evidence; optional `folder_path` (exact folder) and `matching_only` |
| POST | `/evidence` | Create with `folder_path`, `title`, `text`, optional `use_for_matching` |
| GET / PATCH / DELETE | `/evidence/{id}` | Read/edit/delete one owned evidence block |
| GET | `/matches/{id}/evidence` | List library evidence and checked IDs for that match |
| PATCH | `/matches/{id}/evidence` | Replace checked selection with `{"checked_ids":["uuid", ...]}` |

POST evidence generates UUIDv7 on the server. Authored text is preserved; blank text
is rejected. PATCH evidence permits title, text, and matching checkbox only; omitted
fields survive, nulls are rejected, and moving evidence is not supported. Profile behavior is documented in `profile-backend.md`. DELETE evidence removes
its match links through the schema's cascade without rewriting application snapshots.

Selection responses contain `match_id`, `initialized_at`, `evidence`, and `checked_ids`.
The evidence list contains the owner's whole library, so the UI can show unchecked
items. Saving `checked_ids: []` is valid and still records successful initialization.
Missing/foreign-owned resources return 404, folder conflicts return 409, invalid inputs
return 422, and an uninjected evidence service returns 503. The API remains local and
single-user without authentication. A match-level timestamp is not multiuser isolation.

## Boundaries and concurrency

Controllers depend on `EvidenceServiceProtocol`. `EvidenceService` assigns identity,
creates UUIDv7 IDs through the domain draft, validates paths, and depends on
`EvidenceRepository`. Evidence content, edits, and selections are defined in
`src/shared/models/evidence.py`: `EvidenceDraft`, `NewEvidence`, `EvidenceUpdate`,
`Evidence`, `EvidenceFolder`, `MatchEvidenceUpdate`, and `MatchEvidence`. HTTP input
names alias the corresponding domain types. Services pass typed commands directly
to the repository; they do not create storage dictionaries or JSON strings.

`EvidenceRepositoryPostgres` owns SQL, JSON conversion, and transactions. Its private
`_EvidenceRow`/`_SelectedEvidenceRow` adapters keep the stored owner and joined checkbox
columns local, returning plain domain evidence and selection objects. A private typed
career-row projection decodes JSONB when checking profile links before deletion.
Repository protocols never expose these local row types or generic field mappings.
This boundary refactor was checked offline only, without changing databases or migrations.
Analyst wiring
constructs the service/repository from the existing pool; no controller accesses SQL.

Library mutations lock the owner row in PostgreSQL, coordinating across requests and
processes. Parent validation and folder deletion therefore cannot race through these
repository methods. Paths are compared literally (including `%` and `_`), not with an
unescaped LIKE expression. Folder deletion checks evidence and every descendant path.

Selection replacement validates all IDs before changing state and locks the match.
Invalid IDs roll back the entire request. Selection reads use a consistent transaction.
The current single configured owner is required: match IDs do not encode user ownership.
Updates replace the full selection; concurrent browser edits use last committed write.

`matching_materials()` returns current matching-enabled content as `Material` objects.
`selected_materials(match_id)` returns current checked content, resolving text on each
call. They are the integration boundary for subsequent prompt/selector work.

## Remaining integration

The frontend now exposes `/dossier` from the sidebar. It uses `/api/folders` and
`/api/evidence` for a nested folder/file tree and plain-text editor, including save,
delete, loading/retry states, and a matching checkbox. Profile no longer embeds the
old dossier editor; `/profile?tab=dossier` redirects to `/dossier`. The new page does
not use the legacy browser-local dossier store or automatically import its contents.

These endpoints persist choices but **do not yet change matching/discussion/generation
prompts**. Existing runtime consumers still use `ProfileRepositoryXML`. Jev's current
selector returns XML paths, while new evidence uses UUIDs; an explicit import/mapping
and database-backed profile/selector adaptation must precede automatic initialization.
Manual selection already records initialization and must be preserved by that later
integration.

## Separate application repository

`src/database/postgres/application_packet_materials.py` contains the optional
`ApplicationPacketMaterialsRepositoryPostgres`. It is deliberately absent from
production imports, package exports, and analyst wiring. The existing repository
continues reading/writing `materials_snapshot` and decoding it as `ApplicationPacket.materials`.

The alternate repository explicitly requires `applications.materials UUID[]` and
`applications.user_id`. It validates owned UUID references on save and resolves current
evidence text on detail/list/pending/update reads. Resolution of a page uses one evidence
query. Missing or foreign-owned evidence fails explicitly; it never falls back to the
stored snapshot. Deleting an evidence block can therefore make an older referenced
draft unreadable through this alternate repository. This behavior needs review before
any future production switch. No conversion or schema migration is run by the class.

Offline tests are in `tests/test_application_packet_materials.py`. CRUD was also
verified against a synthetic PostgreSQL database with UUID-array application materials;
the legacy snapshot repository was checked against the same records and remained unchanged.

## Dossier browser verification

The built frontend was exercised in a headless browser against the actual evidence API
and a separate `evidence_dossier_ui_test` PostgreSQL database. Browser API traffic was
intercepted and forwarded exclusively to that test API, without invoking the analyst or
connecting to `dorker_db`. Checks covered nested folder creation, file creation/edit/save,
reload persistence, matching-checkbox persistence, failed-save text preservation, unsaved
editor switching, deletion, nonempty-folder conflicts, sidebar/profile routing, legacy
URL redirect, load retry, mobile overflow, and JavaScript errors. Desktop/mobile images
were inspected. The temporary API/frontend/database processes were stopped afterward.

## Verification

Offline API/service tests:

```sh
python -m unittest discover -s tests -p test_evidence.py
```

The opt-in PostgreSQL test reads only `EVIDENCE_TEST_DSN`, never `PG_DSN`, and rejects
database names outside `evidence_*_test`. Use a dedicated disposable database with
migration 012 installed and minimal `jobs(id, deleted_at)` and `matches(id, job_id)`
tables. Do not use a copy containing personal data; fixtures use fixed test-owner IDs.

```sh
EVIDENCE_TEST_DSN='postgresql:///evidence_service_test?host=/tmp/dorker-evidence-validation&port=55439' \
  python -m unittest discover -s tests -p test_evidence_postgres.py
```

Tests cover owner isolation, UUIDv7, profile persistence, parent and nonempty-folder
checks, concurrent folder operations, literal path characters, selection rollback,
empty initialization, current text after edits, and link cleanup after deletion.
