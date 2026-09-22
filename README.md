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
Run `docker compose -f deployment/docker/docker-compose.yml up --build -d`. Apply migrations within `migrations/postgres` folder and run `fill_ats.py` script, to populate **companies** table. Then run command from root: `python -m orchestration.scraper`. 

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

