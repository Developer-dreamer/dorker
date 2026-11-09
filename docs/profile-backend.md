# Reusable application profile

Status: implemented for review; migration 012 is prepared but **has not been applied**.
No PostgreSQL connections or migrations were used to verify this profile expansion.
Application storage still uses `materials_snapshot`; profile fields are not automatically
sent to matching, discussion, or generation providers. No XML/local-profile import is included.

## Storage

There is one `users` row per existing bigint identity. Scalar contact, residence,
availability and mobility facts have dedicated nullable columns. Simple lists use TEXT[];
unknown yes/no answers remain SQL NULL, independently of false. The old `name` remains
an independent display name and is never split into first/last names. Existing
`profile_links` remain supported as additional URLs.

Employment, education, languages, country-specific authorization, compensation and
resumes are JSONB arrays validated by Pydantic models in `src/shared/models/profile.py`.
Records retain supplied UUIDs; the backend assigns UUIDv7 when an ID is omitted. The UI
assigns stable UUIDs to new editor rows so removing/reordering entries cannot change
another entry's identity. Employment/education dates have month precision (YYYY-MM).
Currency is an explicit uppercase three-letter code; amounts are never converted.
Country values are ISO 3166-1 alpha-2 codes, not regions such as EU. Timezones are IANA
names. Phone numbers are normalized to international +digits form.

Availability is an explicit nullable domain field: `immediately`, `from_date`, or
null (not provided). Only `from_date` carries `earliest_start_date`; it requires a date.
Changing availability to immediate or unknown clears the saved date in the same update.
Notice period remains independent and never implies availability. Historical earliest
dates remain valid lower bounds. The editor offers these three choices and shows a
required date picker only for `from_date`; immediate availability displays “Available now”.

Migration `013_profile_availability.sql` is prepared, not applied. It follows either
the expanded users schema from 012 or the manual users replacement. It adds the scalar
field and consistency constraint, classifying existing dates as `from_date` without
inferring immediate availability. Apply it before testing the new availability editor.

Users may save an incomplete profile. A compensation entry still requires its currency,
period and gross/net basis; a resume requires its label and HTTP(S) URL; a work-authorization
entry requires its country. A current employment entry cannot have an end month.
The optional default resume UUID must reference a resume entry in the same row. Nothing
claims that a linked resume file has been uploaded or successfully downloaded.

## API and layering

- `GET /api/profile` returns the complete profile with nulls/empty lists for unknown data.
- `PATCH /api/profile` updates supplied fields only. Optional scalar nulls clear values;
  arrays replace their respective lists, and cannot be null. No owner ID is accepted.
- `GET /api/profile/options` supplies ISO country codes and available IANA timezones.
- `PATCH /api/profile/{collection}/{id}/evidence-links` replaces a saved career record's
  links using `{"evidence_ids": ["uuid", ...]}`. An empty list detaches all files.
- `POST /api/profile/{collection}/{id}/evidence` creates and attaches evidence atomically,
  accepting the same folder_path/title/text/use_for_matching body as evidence creation.

Collections are `employment_history` and `education`. Records must be saved before the
link endpoints can address them. Missing or foreign-owned evidence/records return 404;
invalid merged profile state returns 422. Existing name/profile_links PATCH clients
continue to work. Cross-field checks use the complete merged row, so removing an active
resume also requires clearing or replacing its default selection in the same PATCH.

The profile controller depends on ProfileServiceProtocol, which scopes operations to
the configured local user and delegates persistence to ProfileRepository. PostgreSQL
implementation lives in `src/database/postgres/profile.py`. Profile persistence has
been removed from the evidence service/repository. Both repositories use the same
owner-row transaction lock to coordinate profile-link updates with evidence deletion.

Accepted boundary: `src/shared/models/profile.py` owns `UserProfile`, the individual
career/authorization/compensation/resume record types, and typed mutations
(`ProfileUpdate`, `CareerRecordReference`, `EvidenceLinks`). The HTTP input names are
aliases of these domain models. Handlers and services exchange these objects directly;
services do not dump them into dictionaries or JSON before calling repositories.
`ProfileRepository` accepts and returns domain types only.

Inside the PostgreSQL repository, private `_ProfileRow` handles driver JSON strings,
merges supplied update fields, validates the complete resulting profile, and encodes
database parameters. Only the repository knows which columns use JSONB. It converts
rows back to plain `UserProfile` objects before returning. Nested records remain typed
throughout the service flow, and generated IDs survive JSONB encoding unchanged.
Omission is tracked on `ProfileUpdate`; explicit scalar nulls still clear fields.
This boundary refactor changes neither SQL schema nor HTTP payload fields and requires
no additional migration. Its checks use mocked connections only.

## Dossier links

Career records and evidence files have independent identities. A career record may
reference several evidence files, and a file may support several records. Empty links
are valid, including on first use. Creating evidence and adding its ID to the record
uses one transaction; failed validation propagates out of that transaction. Existing
links are validated for ownership before saving.

Evidence deletion is rejected with 409 while a saved employment/education record links
to it. The message identifies the referencing company/institution (or record ID). Detach
first through Profile; detaching does not delete either object. These are application
invariants: JSONB references are not database foreign keys, and direct SQL can bypass them.

The frontend opens linked files through `/dossier?evidence=<uuid>`, expands their folder
ancestors, and selects the evidence text. Unavailable links display an error. Links are
not inferred from matching titles, and neither first-use placeholders nor XML imports
are created automatically.

## Frontend and verification

Contact & links, Work preferences, Career, and Documents each have independent Edit,
Save and Cancel controls. Inputs show Not provided rather than coercing missing answers
to no/false. Failed saves preserve drafts and show an inline error. All profile reads
and saves use the backend GET/PATCH contract; the old browser-local fallback has been
removed from active code. Existing browser storage is left untouched. The sidebar user
menu reads the same API-backed profile and contains no sample identity fallback.

Offline validation, service/API, and mocked repository tests:

```sh
python -m unittest discover -s tests -p test_profile.py
python -m unittest discover -s tests -p test_evidence.py
```

Frontend build and browser verification use a temporary in-memory implementation behind
the actual profile handler/service and synthetic data. This verifies UI interaction,
serialization, links and errors, but **does not establish SQL migration compatibility or
PostgreSQL integration**. Those checks are deliberately deferred until the maintainer
approves migration execution. Neither the live nor temporary PostgreSQL schema was changed.
