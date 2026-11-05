# Dorker
> App used to simplify searching and application process

## Stage
**Under an active development**. 

Current tasks:
- Developing local ML matching pipeline
  - Optimizing entity extraction and deterministic job scoring

## Description
This app is an application engine, which is responsible for scraping public ATS systems (forked from: [kalil0321/ats-scrapers](https://github.com/kalil0321/ats-scrapers), so go check it up supported scrapers there) and application matching.

## Run
Run `docker compose -f deployment/docker-compose.yml up --build -d`. Apply migrations within `migrations/postgres` folder and run `fill_ats.py` script, to populate **companies** table. Then run command from root: `python -m orchestration.scraper`.

## Local application API

`python -m orchestration.analyst` serves a Litestar ASGI application through Uvicorn,
alongside the matching and summary workers. Install API dependencies with
`python -m pip install '.[api]'` (also declared in `environment.yml`).
The server binds to `127.0.0.1:8000`; `API_PORT` overrides the port.
[Swagger UI](http://127.0.0.1:8000/docs) and
[OpenAPI JSON](http://127.0.0.1:8000/openapi.json) describe the exposed contracts.
Vite origins `http://localhost:5173` and `http://127.0.0.1:5173` are allowed.
CORS preflight succeeds with Litestar's native 204 response.
This is a local single-user API without authentication.

`src/api/app.py` registers four Litestar controller classes: Job, Match, Discussion,
and Application. Litestar replaces FastAPI to give these classes native controller
semantics and shared domain configuration without registration in constructors.
Controllers declare routes on their methods; the app injects service
protocol implementations through named dependencies. Python attributes and JSON fields
use snake_case. The analyst constructs the persisted services in `src/api/services/`
and injects all four into `create_app`. The existing PostgreSQL repositories provide
pagination and domain models. Database JSON decoding/encoding stays in repositories. Match browsing
and counts use the configured model/minimum version and latest eligible match per job;
soft-deleted jobs/drafts are excluded. Joined company identity avoids per-job lookups.
Calling `create_app()` directly without services still returns 503 for valid requests.
Invalid inputs return 422. Tests use synthetic repositories and providers.
No endpoint directly constructs analytics providers or accesses repositories.
Routes match raw ID segments and then validate UUID/integer parameters, preserving
422 responses for malformed IDs rather than rejecting them as route-level 404s.

The defined routes provide paginated job and match browsing, match counts/debug/reports/status,
conversation history/start/messages/stream/finish, and application draft creation,
listing, retrieval, editing, and selective regeneration. Applications have integer IDs;
matches have UUIDs. Generation completes within the request; there is no background
operation or employer submission endpoint. Draft PATCH requests preserve omitted fields;
regeneration replaces only selected sections, using the draft's saved candidate materials.
Generation finishes before a new draft is inserted or existing sections are updated, so
provider failures preserve saved drafts. Edits and regeneration of one application are
serialized within this server process. Run one API worker; these locks do not coordinate
multiple processes. Attachments and confirmed evidence are deferred.

Discussion requires explicit start and cannot reopen after finish. The app wraps an
injected discussion service once with `SerializedDiscussionService`: mutations of the
same discussion wait for the current turn to complete, fail, or be cancelled. Other
discussions and reads proceed independently. The stream owns its turn lock from
preflight through closure, including disconnect cleanup. This coordinates requests
in one process; it is not a database or cross-worker lock. Streaming replies use
NDJSON: `accepted` carries the persisted user message, `delta` carries provisional text,
and `done` carries the complete persisted assistant message. An `error` event includes
`saved`, indicating whether the user message was persisted. Before streaming headers,
missing matches and lifecycle conflicts return normal HTTP errors. On disconnect the
stream iterator is closed. Reload history before retrying uncertain deliveries. History
DTOs exclude internal initialization context, provider metadata, and identity fields.

Configure existing database and provider credentials (`PG_DSN`, `OPENAI_API_KEY`, and
TypeSafe SDK credentials) for the analyst workers and generation endpoints. Discussion
uses `TELEGRAM_CHAT_ID` (default `975840884`) as the local user/chat identity to retain
existing history; HTTP requests cannot override it. Existing database migrations remain
required. Inspect the analyst entry point before running it: workers can access databases
and external providers. The HTTP adapter tests run offline without starting those workers.

The Telegram interaction instructions below describe the retained legacy client; it is no
longer started by the analyst entry point.

## Application discussions

Apply `migrations/postgres/sql/009_discussion_messages.sql` before running the analyst bot.
It adds one table and indexes; it does not alter existing tables or relationships.
Apply `migrations/postgres/sql/010_match_reports.sql` before using match reports;
it adds only the `reported` and `report_message` columns to `matches`.
The legacy Telegram client uses `OPENAI_API_KEY`, `TELEGRAM_API_KEY`, and `TELEGRAM_CHAT_ID` (your private
Telegram user/chat ID; defaults to the existing configured ID). `OPENAI_DISCUSSION_MODEL`
defaults to the same `gpt-6-luna` model already used for application generation.
The analyst command now starts the local API described above.
The same process runs job classification immediately on startup and repeats it one hour
after each cycle finishes, alongside HTTP serving. Each cycle uses the existing
eligible/unmatched-job query and processes up to 1,000 jobs. A failed cycle is logged
and retried at the next interval; stopping the server cancels the classification loop.

Accepted matches are saved immediately. A separate summary worker calls OpenAI
`gpt-6-luna`, keeping Jev classification and HTTP serving responsive. PostgreSQL is the
summary queue: non-rejected matches with a NULL or blank summary at or above the
minimum accepted pipeline version are processed on startup, on match notifications,
and every 60 seconds. Failed or interrupted summaries remain pending for retry.
Only the job title, location, and description are sent for summary generation;
no profile or discussion context is included. The summary retains its JSON
`summary`/`pros`/`cons` format. A match may appear in Telegram before its summary is ready;
an already-sent message is not edited automatically.

On a job card, select **Discuss with AI**, then send questions as ordinary messages.
Before discussion starts, Jev selects relevant profile materials using only `profile.xml`
and the job description. One `ProfileRepositoryXML` loads that file and is shared by
classification, discussion, and manual material lookup through the abstract `ProfileRepository`.
The discussion includes the CV and search preferences from that XML, the job, Jev-selected
materials, and the match's scores/tier/summary. Keep residency and contract constraints in
the XML's CV/search parameters. Separate `cv.md` and `discussion_context.md` files are not read.
The complete XML, unselected coursework/dossier blocks, and raw debug are not sent
automatically to the chat model.
Each discussion is tied to that match and Telegram user/chat.

- `/context <text>` attaches pasted information for discussion only.
- `/use <text>` explicitly confirms a correction or experience detail for the application.
- `/materials [filter]` lists up to 40 matching paths from the configured profile.
- `/material <path>` explicitly includes that profile block in the application.

Select **Finish discussion**, then **Prepare application** or **Ignore**. Preparation
uses the existing questions/cover-letter flow and the same saved material selection,
without running profile classification again. Confirmed additions override selections
with the same profile path and are persisted in the existing application materials/debug
fields; unconfirmed conversation and assistant suggestions are not passed to generation.
Jev's match and tier remain unchanged. User actions and model replies (including API
usage and response output) are saved in `discussion_messages` for later analysis.

All messages are saved. Model requests use the CV/preferences/job/selected materials plus recent complete
turns (up to 15 messages and 24,000 characters, retaining at least the current turn),
with confirmed additions included separately so they survive the window. Active input
mode is in memory: finish before restarting the bot. Reopening discussions is not
supported in this first version. Arbitrary file/table access is not supported; paste
additional debug information with `/context`.

Run the discussion tests without API calls: `python -m unittest discover -s tests`.

---


### Match reports

Use **Report** on the current job card, the main/discussion menu, or `/report`.
The prompt identifies the job. Sending non-empty text atomically sets
`matches.reported = TRUE` and `report_message`; a later report replaces that message.
**Cancel** or `/cancel` abandons the pending input without changing saved feedback.
Reporting preserves classification and application status and resumes any paused
discussion or application-question entry. Feedback is not sent to the AI or used as
application material. Navigation is blocked while a report is pending; old Report
buttons cannot target a different current job, and old Cancel buttons cannot cancel
a newer prompt. Unsubmitted prompts are discarded on restart; submitted reports persist.

These are accepted behaviors for the first report implementation. Review suspected
classification errors with `SELECT * FROM matches WHERE reported`, adding the desired
pipeline-version filter. Unreported matches are unreviewed, not verified correct.


## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPLv3)** - see the [LICENSE](LICENSE) file for details.

### Commercial Licensing
If you wish to use this software in a proprietary or closed-source environment without adhering to the AGPLv3 requirements, commercial licenses and enterprise support options are available.

Contact us at: `podolancukserafim@gmail.com`

---

## ⚠️ Legal Disclaimer

**This software is provided for educational and research purposes only.**

### No Warranty

THIS SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS, CONTRIBUTORS, OR COPYRIGHT HOLDERS (INCLUDING EVER CO) BE LIABLE FOR ANY CLAIM, DAMAGES, OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT, OR OTHERWISE, ARISING FROM, OUT OF, OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

### Data Collection & Unofficial API Risks

Several source modules in this project interact with third-party websites using **unofficial, undocumented APIs** or **HTML parsing techniques**. By using these modules, you acknowledge and accept the following risks:

- **Account suspension or ban** — Your user accounts on job boards (LinkedIn, Indeed, Glassdoor, etc.) may be temporarily or permanently suspended if the platform detects automated access that violates their Terms of Service.
- **IP blocking** — Your IP address may be rate-limited or blocked by target websites.
- **Terms of Service violations** — Automated data collection may violate the Terms of Service of the target platforms. It is **your responsibility** to review and comply with each platform's ToS before using the corresponding source module.
- **Data usage restrictions** — Job listing data obtained through this software may be subject to copyright or other legal protections. You are solely responsible for ensuring your use of the data complies with all applicable laws and regulations.

### Limitation of Liability

The contributors to this project:

- **Do not endorse or encourage** the violation of any website's Terms of Service.
- **Are not responsible** for any consequences resulting from the use of this software, including but not limited to account bans, legal action, data loss, or financial damages.
- **Make no guarantees** about the accuracy, completeness, or reliability of the collected data.
- **Accept no liability** for how this software is used by third parties.

**Use at your own risk.** If you are unsure about the legality of automated data collection from a particular website in your jurisdiction, consult a legal professional before proceeding.
