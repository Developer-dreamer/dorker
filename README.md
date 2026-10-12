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

## Application discussions

Apply `migrations/postgres/sql/009_discussion_messages.sql` before running the analyst bot.
It adds one table and indexes; it does not alter existing tables or relationships.
Configure `OPENAI_API_KEY`, `TELEGRAM_API_KEY`, and `TELEGRAM_CHAT_ID` (your private
Telegram user/chat ID; defaults to the existing configured ID). `OPENAI_DISCUSSION_MODEL`
defaults to the same `gpt-6-luna` model already used for application generation.
Start the bot with `python -m orchestration.analyst`.
The same process runs job classification immediately on startup and repeats it one hour
after each cycle finishes, alongside Telegram polling. Each cycle uses the existing
eligible/unmatched-job query and processes up to 1,000 jobs. A failed cycle is logged
and retried at the next interval; stopping the bot cancels the classification loop.

Accepted matches are saved immediately. A separate summary worker calls OpenAI
`gpt-6-luna`, keeping Jev classification and Telegram responsive. PostgreSQL is the
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
