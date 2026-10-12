# Concurrent analyst services

Status: accepted. Job classification and Telegram polling run in one analyst process.

`orchestration.analyst.run_services` starts a classification task while awaiting the
Telegram bot. Classification runs immediately, then waits 3,600 seconds after each
completed or failed cycle. Sequential cycles cannot overlap. Database/query failures
are logged and retried after that interval; cancellation propagates without retry.
Existing per-job failure handling remains unchanged.

The existing query reads eligible jobs without a match at or above the accepted
pipeline version. The five-week date window, filters, normalization requirement,
1,000-job cycle limit, and run-level iteration number are unchanged. Newly scraped
jobs become candidates once they satisfy those conditions; new matches are outputs
of classification rather than its input.

When Telegram stops or fails, the classification task is cancelled and awaited
before shared provider clients and the database pool are closed. Logging setup stays
in the CLI entry point so importing the orchestration helpers does not open log files.
This introduces no scheduler dependency or database migration. Summary restart recovery
is described below.

Acceptance examples: Telegram can receive input during a classification batch; a
failed cycle does not prevent the next scheduled cycle; stopping Telegram during a
batch or its hourly wait leaves no classification task running.


## Independent OpenAI summaries

Status: accepted. Supersedes the inline SLM summary step.

Jev saves the match before notifying the summary worker through a shared asyncio
Event. It never awaits summary generation. The Event is a wake-up hint; PostgreSQL
owns pending work. No database tables or relations are changed.

The repository selects non-rejected matches with NULL or blank `job_summary`, at or
above the configured minimum pipeline version, whose jobs have not been deleted.
There is no application-status or five-week filter on summary recovery. Existing
summaries are preserved, including older SLM summaries. This recovers existing
missing summaries as well as newly classified matches, without changing decisions
or reopening applications.

One worker checks immediately on startup, on notification, and every 60 seconds.
It processes up to 100 IDs per query and drains successful full batches immediately.
API or database failures are logged; missing summaries stay pending and other items
in the batch continue. A failed batch waits for the next notification or polling
interval before retrying. No database connection is held during the network call.

The async OpenAI Responses API uses `gpt-6-luna` and structured outputs, retaining
`summary`, `pros`, and `cons` as JSON in the existing text column. Only job title,
location, and description leave the process for this purpose. No profile, classifier
debug output, or discussion history is sent. The local SLM is no longer instantiated
by the analyst; its standalone implementation remains available in its module.

Telegram shutdown cancels and awaits classification and summary tasks before closing
the summary client and database pool. Interrupted work is recovered next startup.
Delivery is at least once: a crash after an API response but before the database
update can cause a repeated API request. This is a single-analyst worker, without
distributed claiming across multiple analyst processes.

Matches may be visible in Telegram before their summaries finish. The existing
empty-summary rendering remains supported; previously sent messages are not edited.
Acceptance: classification completes additional jobs while an API call is pending;
rejected/unsaved matches do not notify the worker; startup finds pending database
work without an Event; failures preserve pending work; shutdown cancels API work.
