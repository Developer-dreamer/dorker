# Profile repository integration

Status: accepted for the XML implementation. PostgreSQL support is deferred.

The maintainer's `ProfileManager` sketch becomes `ProfileRepositoryXML`, deriving
from the abstract `ProfileRepository`. The abstraction exposes profile sections,
selectable materials, lookup by material ID, and a full textual profile dump.
Classifiers own relevance decisions; the repository owns reading and identifying
profile data. No new database tables or relationships are introduced.

The analyst entry point creates one repository from `artifacts/data/prompts/profile.xml`
and shares it with the matching engine and discussion service. This replaces separate
CV loading, orchestration-level XML extraction, and discussion's independent material
registry. Generation instructions remain a separate prompt; candidate profile data
comes from the XML repository.

Data sent to model providers:

- Job classification: CV and search preferences, followed by the job payload.
- Profile selection: full XML and job description, using existing questions/thresholds.
- Discussion: CV, search preferences, selected materials, job, and compact match assessment.
  Raw match/selection debug remains in stored discussion metadata.
- Generation: saved selections plus explicitly confirmed additions; the existing
  application packet is persisted only when generation begins.

The repository is a read-only in-memory snapshot loaded once per analyst run. Editing
the XML requires restarting the analyst. A future PostgreSQL implementation can load
the same abstraction without exposing storage details to its consumers. There is no
backend factory, write API, or database implementation in this slice.

Material IDs retain XML ancestry and identifying attributes. Coursework accepts `id`
when `code` is absent. Missing identifiers and duplicate IDs fail explicitly rather
than silently replacing a selection. Text extraction keeps boundaries between nested
sections. Legacy saved materials using `path` are accepted as `id` without changing
database schema or rewriting saved rows.

Acceptance examples:

- A cover-letter selection can retrieve every letter through the repository.
- An unselected course remains available to `/material`, but is absent from initial chat context.
- A confirmed addition survives the history window and reaches generation alongside the saved selection.
- A discussion serializes CV/preferences as text, rather than serializing a `Material` object.
- Generation after discussion does not repeat profile classification.

Unchanged limitations: forward-only flow, in-memory active Telegram mode, and Jev's
profile-plus-description context limit. Crash recovery and context batching are separate work.
