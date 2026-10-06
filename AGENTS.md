# Working on Dorker

Dorker scrapes job listings, matches them to a candidate, and supports application
preparation and discussion through Telegram. The maintainer owns product behavior
and architectural decisions. Agents carry out implementation and verification
within an agreed scope.

## Align before implementing

- Read the relevant code and task references first. Describe existing behavior
  separately from intended behavior; point out contradictions and uncertainty.
- For ambiguous features or architectural changes, present a short implementation
  brief before editing: intended behavior, consequential choices, affected
  components, scope exclusions, and concrete acceptance examples. Recommend an
  approach and explain the tradeoff. Wait for agreement on unresolved choices.
- Examples of consequential choices: state ownership and restart behavior, schema
  changes, public interfaces, new runtime dependencies, model/provider changes,
  scoring semantics, and what candidate data is sent to external services.
- An explicit task that already settles these choices authorizes execution.
  Routine fixes and documentation edits can proceed after a concise scope update.
  Resolve local implementation details independently; avoid repeated approval
  requests for decisions already made.
- Implement a small, reviewable slice first when the behavior is still being
  explored. Return to the maintainer if new evidence requires changing the agreed
  behavior or architecture. Continue independent investigation while clarifying.
- Keep unrelated refactors and improvements out of the task. Report them
  separately. Preserve existing user changes and inspect Git status before editing.

## Keep decisions available

- Use the current task and explicitly accepted decisions as the behavioral
  reference. Existing code is evidence of implementation, not proof of intent.
- Read the Notion pages, diagrams, or repository documents named in the task.
  If access fails, report the missing context and its impact; do not invent it.
- Record consequential accepted decisions in the task's designated document:
  decision, reason, affected components, and status (accepted or superseded).
  Keep proposals and unresolved questions clearly labeled.
- Use Notion for collaborative documentation and Excalidraw for editable diagrams
  when requested. Do not assume external documents stay synchronized with code.
  Identify the reference used and update the authorized documentation destination.
- Keep this file focused on durable working agreements. Feature-specific details
  belong in their task brief or design document. Propose changes to project-wide
  rules when repeated corrections reveal a useful convention.

## Repository map

| Location | Responsibility |
| --- | --- |
| `orchestration/scraper.py` | Configures and runs scraping, persistence, CLI progress, and telemetry |
| `orchestration/normalizer.py` | Normalizes stored job descriptions in PostgreSQL |
| `orchestration/analyst.py` | Wires analytics, model providers, discussion, repositories, and Telegram |
| `src/scraping/` | ATS adapters, fetching, scraping pipeline, configuration, and scraping repositories |
| `src/analytics/` | Matching, classification, profile materials, generation, and discussion |
| `src/client/telegram.py` | Telegram commands, callbacks, and user interaction |
| `src/database/` | Analytics repository protocols and PostgreSQL implementations |
| `src/shared/models/` | Shared data models and pipeline version information |
| `migrations/` | PostgreSQL and legacy SQLite migration scripts |
| `configs/`, `artifacts/` | Scraper configuration, prompts, datasets, and model artifacts |
| `tests/` | Currently unavailable, will be configured and maintained soon |

Follow the surrounding async Python, typed models, and protocol/repository patterns.
Inspect imports before copying examples: the checkout currently uses both `src.*`
and package-level imports. Avoid broad import or packaging rewrites as side effects.

Scrapers derive from `kalil0321/ats-scrapers`. Read
`.github/workflows/sync-scrapers.yml` before modifying synchronized adapters or tests,
and consider how local changes survive upstream synchronization. Preserve attribution.

## Environment and validation

- Run commands from the repository root in the project's Python environment.
  `environment.yml` describes the Conda environment and currently specifies Python
  3.13; some source syntax requires Python 3.12 or later. Check the active interpreter
  and dependencies before diagnosing environment failures as application defects.
- `pyproject.toml` configures Ruff (100-character lines, Python 3.12 target) and
  strict mypy. Use the existing configuration rather than weakening checks.
- Available validation commands, subject to installed project dependencies:
  - Mocked discussion tests: `python -m unittest discover -s tests`
  - Lint changed Python files: `python -m ruff check <changed-python-files>`
  - Type checks: `python -m mypy --no-incremental` (configured for `src` and `orchestration`)
- These commands are guidance, not a claim that the current checkout passes them.
  Distinguish existing failures, environment limitations, and regressions from the task.
- For behavior changes, verify agreed acceptance examples and meaningful failure
  cases. Mock network/model calls where possible. Tests should exercise intended
  behavior rather than merely reproduce the implementation's assumptions.
- For documentation-only changes, verify paths, commands, and consistency; no
  application test run is required. After relevant checks pass, repeat them only
  when changes or unresolved concerns justify it.

## Runtime and documentation accuracy

- The Compose file is `deployment/docker-compose.yml`; it defines PostgreSQL and
  backups. Python entry points run separately: `python -m orchestration.scraper` and
  `python -m orchestration.analyst`.
- Inspect entry points before running them: they can access databases, job sites,
  Telegram, AI providers, local model files, and telemetry. Starting the application
  or applying a migration is not a routine offline test.
- In architecture diagrams, distinguish Python modules, running processes, external
  services, and storage. Label communication type and data exchanged. Mark optional,
  disabled, and proposed paths; inspect the current wiring rather than assuming all
  implemented methods run (background classification is currently commented out
  in the analyst entry point).
- Use synthetic candidate examples in public documentation and tests. Keep private
  profile materials, credentials, and personal discussion data out of public artifacts.

## Completion and handoff

- Review the diff against the agreed scope and acceptance examples before reporting
  completion. Explain changed behavior, consequential decisions, checks actually run,
  and any remaining limitations. Never claim a check or integration was verified
  without evidence.
- Leave changes reviewable. Commit, publish, merge, deploy, or write to external
  documentation when that action is authorized by the task.
- Add skills or recurring automations after the workflow proves useful in practice;
  give each a bounded purpose and a clear completion condition.
