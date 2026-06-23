import asyncio
import logging
import os
import pathlib
import re
import sys
from datetime import datetime
from typing import Any, Literal, cast

import aiosqlite
import httpx
import polars as pl
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, JsonValue


def evaluate_location_relevance(location_str: str) -> str:
    """
    Analyzes a location string based on a real-world dataset of 3.7M vacancies.
    Rejects raw non-remote geographic data and filters localized exclusions.
    """
    if not location_str:
        return "REJECT_EMPTY"

    # 1. STANDARDIZATION
    location_raw = location_str.strip()
    location = location_raw.lower()

    # ==========================================
    # STEP 1: CONTEXT SECURITY GATE (Eliminate pure On-Site/Hybrid data)
    # ==========================================
    # If the string doesn't say "remote" or "wfh", it's an office job city/hub. Reject it.
    remote_context_tokens = ['remote', 'wfh', 'home', 'anywhere', 'worldwide', 'world wide', 'global', 'emea', 'europe']
    if not any(token in location for token in remote_context_tokens):
        return "REJECT_NO_REMOTE_CONTEXT"

    global_markers = ['worldwide', 'global', 'world wide', 'emea', 'europe', 'international', 'latam', 'apac', 'utc'] # maybe should add 'amer' but not for me obviously
    if any(re.search(r'\b' + re.escape(w) + r'\b', location) for w in global_markers) or re.search(r'\b(eu|world)\b', location):
        return "KEEP_GLOBAL"

    # ==========================================
    # STEP 2: HARD EXCLUSIONS (Negative Filters First)
    # ==========================================

    # A. US Specific Phrases & Cities
    us_specific_words = [
        'united states', 'america', 'usa', 'u.s.', 'nationwide', 'any state', 'san francisco',
        'sanfrancisco', 'sf', 'austin', 'boston', 'chicago', 'seattle', 'atlanta', 'new york', 'nyc',
        'los angeles', 'la', 'silicon valley', 'bay area', 'bayarea', 'socal', 'southern california',
        'denver', 'dallas', 'houston', 'miami', 'philadelphia', 'phoenix', 'portland', 'noram', 'us_remote',
        'columbus', 'leawood', 'dtla', 'maryland', 'sunnyvale', 'redwood city', 'mclean', 'arlington', 'san diego'
    ]
    if any(w in location for w in us_specific_words) or re.search(r'\b(us|usa|u\.s|sf|nyc|la)\b', location) or location.endswith('-united-states'):
        return "EXCLUDE_US"

    # B. US Case-Sensitive 2-Letter State Codes
    state_codes = [
        'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA', 'HI', 'ID', 'IL', 'IN', 'IA',
        'KS', 'KY', 'LA', 'ME', 'MD', 'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
        'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX', 'UT', 'VT',
        'VA', 'WA', 'WV', 'WI', 'WY', 'PR', 'DC', 'OH'
    ]
    if re.search(r'\b(' + '|'.join(state_codes) + r')\b', location_raw):
        return "EXCLUDE_US_STATE"

    # C. Full US State Names
    us_states_full = [
        'alabama', 'alaska', 'arizona', 'arkansas', 'california', 'colorado', 'connecticut', 'delaware',
        'florida', 'georgia', 'hawaii', 'idaho', 'illinois', 'indiana', 'iowa', 'kansas', 'kentucky',
        'louisiana', 'maine', 'maryland', 'massachusetts', 'michigan', 'minnesota', 'mississippi',
        'missouri', 'montana', 'nebraska', 'nevada', 'new hampshire', 'new jersey', 'new mexico',
        'new york', 'north carolina', 'north dakota', 'ohio', 'oklahoma', 'oregon', 'pennsylvania',
        'rhode island', 'south carolina', 'south dakota', 'tennessee', 'texas', 'utah', 'vermont',
        'virginia', 'washington', 'west virginia', 'wisconsin', 'wyoming', 'socal', 'south california', 'bay area'
    ]
    if any(w in location for w in us_states_full):
        return "EXCLUDE_US_STATE"

    # D. Non-compatible International Hubs & Countries (Including common spelling variations)
    other_countries_and_hubs = [
        'brazil', 'brasil', 'mexico', 'canada', 'india', 'germany', 'deutschland', 'united kingdom', 'london', 'uk', 'gb',
        'spain', 'philippines', 'colombia', 'poland', 'ireland', 'france', 'australia', 'singapore', 'japan',
        'china', 'netherlands', 'sweden', 'switzerland', 'austria', 'belgium', 'denmark', 'norway', 'finland',
        'argentina', 'portugal', 'serbia', 'romania', 'turkey', 'türkiye', 'slovakia', 'hungary', 'costa rica',
        'chile', 'paris', 'ontario', 'toronto', 'vancouver', 'sydney', 'melbourne', 'berlin', 'munich', 'amsterdam',
        'pakistan', 'italy', 'taiwan', 'south korea', 'cyprus', 'bulgaria', 'vietnam', 'malaysia', 'armenia', 'indonesia',
        'bangalore', 'manila', 'münchen', 'hamburg', 'düsseldorf', 'são paulo', 'sao paulo', 'bogota', 'madrid', 'barcelona',
        'south africa', 'dubai', 'lyon', 'bangkok', 'thailand', 'abuja', 'nigeria', 'seoul', 'derbyshire', 'guatemala', 'leeds',
        'sharjah', 'ae', 'boisbriand', 'taupo', 'home counties', 'manchester', 'birmingham', 'scotland', 'wales',
        'islamabad', 'lahore', 'stellenbosch', 'nürnberg', 'münster', 'kiel', 'bremen', 'ravensburg', 'rankweil', 'reading',
        'zagreb', 'pula', 'lisbon', 'zurich', 'mannheim', 'villach', 'bonn', 'freiburg', 'cork', 'suhl', 'essen', 'fulda',
        'hannover', 'mönchengladbach', 'österreich', 'schweiz', 'neuss', 'mainz', 'stuttgart', 'köln', 'halle', 'leipzig',
        'heidenheim', 'würzburg', 'kassel', 'gießen', 'ludwigshafen', 'schwandorf', 'hengersberg', 'plattlingen', 'karlsruhe',
        'gronau', 'bad hersfeld', 'ingolstadt', 'duisburg', 'neumünster', 'celle', 'minden', 'wolfsbrug', 'göttingen', 'trier',
        'heilbronn', 'ulm', 'augsburg', 'kempten', 'garmisch', 'passau', 'rosenheim', 'fürth', 'landshut', 'burg', 'stendal',
        'colbitz', 'schönebeck', 'zerbst', 'königsborn', 'zeppernick', 'lübars', 'magdeburg', 'bernau', 'eberswalde', 'strausberg',
        'velten', 'potsdam', 'fürstenwalde', 'bremerhaven', 'kaiserslautern', 'saarbrücken', 'salzburg', 'kufstein', 'gelsenkirchen',
        'rendsburg', 'aschaffenburg', 'meiningen', 'koblenz', 'gera', 'aichstetten', 'allgäu', 'lindau', 'stendell', 'gramzow',
        'caselow', 'randowtal', 'schwedt', 'temmen', 'ringenwalde', 'gerswalde', 'joachimsthal', 'prenzlau', 'lunow', 'stolzenhagen',
        'görlitz', 'pinnow', 'penkun', 'tantow', 'angermünde', 'horka', 'dresden', 'chemnitz', 'dortmund', 'salzgitter', 'iserlohn',
        'aachen', 'new delhi', 'tokyo', 'taipei', 'schleswig-holstein', 'tpg zentrale', 'markkleeberg'
    ]
    if any(c in location for c in other_countries_and_hubs):
        return "EXCLUDE_OTHER_COUNTRY"

    # E. ISO 2-letter Country Codes
    country_iso2 = ['br', 'mx', 'ca', 'in', 'de', 'uk', 'es', 'pl', 'fr', 'au', 'sg', 'ie', 'pt', 'tr', 'ro', 'rs', 'pk', 'it', 'tw', 'kr', 'cy', 'bg', 'vn', 'my', 'am', 'id', 'co', 'za', 'lk', 'cn', 'cz', 'th', 'ae', 'ng', 'gt']
    if re.search(r'\b(' + '|'.join(country_iso2) + r')\b', location):
        return "EXCLUDE_OTHER_COUNTRY"

    # F. ISO 3-letter Country Prefixes
    if re.search(r'\b(ind|bra|bgr|arg|deu|aus|can|usa|phl|gbr|fra|esp|mex|col|prt|tha|are|nga|gtm)\b', location):
        return "EXCLUDE_OTHER_COUNTRY"

    # ==========================================
    # STEP 3: INCLUSIONS (Positive Filters Later)
    # ==========================================

    if re.search(r'\b' + re.escape('anywhere') + r'\b', location):
        return "KEEP_GLOBAL"

    # 1. TARGETED LOCAL REMOTE (Highest Positive Priority)
    ukraine_markers = ['ukraine', 'kyiv', 'kiev', 'lviv', 'odessa', 'kharkiv', 'dnipro']
    if any(w in location for w in ukraine_markers) or re.search(r'\bua\b', location):
        return "KEEP_LOCAL"

    # 3. PURE / GENERIC REMOTE
    generic_remotes = {
        'remote', 'remote job', 'remote position', 'remote location', 'any location / remote',
        'remote/homebased', 'remote locations', '1 remote', 'homebased', 'fully remote',
        'remotely based', 'remote office', '100% remote', 'field/remote', 'remote worker - wfh',
        'remote home office', 'remote worker', 'work from home', 'wfh', 'remote; work from home'
    }
    if location in generic_remotes or location.replace(' ', '') == '100%remote':
        return "KEEP_PURE"

    # Explicitly catch strings that say "hybrid" unless they also match local criteria later
    if ('hybrid' in location
            and not any(m in location for m in ['ukraine', 'kyiv', 'kiev', 'lviv'])
            and 'remote' not in location):
        return "REJECT_HYBRID_NON_LOCAL"

    return "POTENTIAL_PURE"

    check = ["Bay Area (hybrid/remote)",
             "Remote or Hybrid",
             "Hybrid / Remote",
             "Hybrid / Remote first",
             "Hybrid/Remote",
             "Remote/Hybrid (SoCal)",
             "Hybrid or Remote",
             "Remote/Hybrid",
             "Remote/ Hybrid-Bay Area",
             "Bay Area / Hybrid / Remote",
             "Hybrid SanFrancisco, or remote outside of SF",
             "Hybrid Remote - Eastern or Central Time Zones",
             "On-Site, Hybrid, or Remote",
             "On-site/ Hybrid / Remote",
             "Remote, or Hybrid SF, NYC, BOS or CHI",
             "Hybrid - DTLA & Remote",
             "Remote,Hybrid",
             "flowit AG (Hybrid),Remote",
             "Hybrid,Remote - Schleswig-Holstein",
             "TPG Zentrale,Hybrid,Remote",
             "Markkleeberg,Hybrid,Remote",
             "On-site / hybrid / remote",
             "On-site / Hybrid / Remote"
             ]
    for i in range(0, len(check)):
        res = evaluate_location_relevance(check[i])
        print(f"{str.lower(check[i])}: {res}")


# ===== MODELS =====
EmploymentType = Literal["FULL_TIME", "PART_TIME", "CONTRACT", "INTERN", "TEMPORARY"]
IsRemote = Literal[
    "REJECT_EMPTY", "REJECT_NO_REMOTE_CONTEXT", "KEEP_GLOBAL", "EXCLUDE_US",
    "REJECT_HYBRID_NON_LOCAL", "POTENTIAL_PURE", "EXCLUDE_US_STATE",
    "EXCLUDE_OTHER_COUNTRY", "KEEP_GLOBAL", "KEEP_LOCAL", "KEEP_PURE"
]

class Job(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ats_type: str = Field(..., description="The target applicant tracking system platform.")
    ats_id: str = Field(..., description="The unique, platform-specific identifier.")
    url: HttpUrl = Field(..., description="The direct public career page URL.")
    apply_url: HttpUrl | None = Field(default=None, description="The dedicated endpoint for submitting applications.")
    title: str = Field(..., description="The unformatted, literal job title.")
    company_slug: str = Field(..., description="The normalized identifier of the hiring entity.")
    location: str | None = Field(default=None, description="The free-form raw location string.")
    is_remote: IsRemote = Field(default="POTENTIAL_PURE", description="Remote classification logic result.")
    employment_type: EmploymentType | None = Field(default="FULL_TIME", description="Normalized employment category.")
    description: str = Field(..., description="Clean, plain-text job description.")
    salary_min: float | None = Field(default=None, description="Evaluated lower bound of base compensation.")
    salary_max: float | None = Field(default=None, description="Evaluated upper bound of base compensation.")
    salary_currency: str | None = Field(default=None, description="ISO 4217 currency code.")
    application_questions: list[JsonValue] | None = Field(default=None, description="Structured dictionary of custom inputs.")
    posted_at: datetime | None = Field(default=None, description="Initial publication timestamp.")
    fetched_at: datetime = Field(default_factory=datetime.utcnow, description="System timestamp for ingestion.")

# ===== CONFIGURATION =====
DATABASE_FILE = "/Users/serafym/Developer/dorker.space/jobs.db"
DATASET_URL = "https://storage.stapply.ai/jobhive/v1/all.parquet"
LOCAL_TMP_FILE = pathlib.Path("./all_jobs_temp.parquet")
BATCH_SIZE = 1000

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SQL_SCHEMA = """
             CREATE TABLE IF NOT EXISTS jobs (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 ats_type TEXT NOT NULL,
                 ats_id TEXT NOT NULL,
                 url TEXT NOT NULL,
                 apply_url TEXT,
                 title TEXT NOT NULL,
                 company_slug TEXT NOT NULL,
                 location TEXT,
                 is_remote TEXT,
                 employment_type TEXT DEFAULT 'FULL_TIME',
                 description TEXT NOT NULL,
                 salary_min REAL,
                 salary_max REAL,
                 salary_currency TEXT,
                 application_questions TEXT,
                 posted_at TEXT,
                 fetched_at TEXT NOT NULL
             );

             CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_ats_composite ON jobs (ats_type, ats_id);
             CREATE INDEX IF NOT EXISTS idx_jobs_search_routing ON jobs (is_remote, employment_type); \
             """

INSERT_QUERY = """
               INSERT INTO jobs (
                   ats_type, ats_id, url, apply_url, title, company_slug, location,
                   employment_type, description, salary_min, salary_max, salary_currency,
                   application_questions, posted_at, fetched_at, is_remote
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(ats_type, ats_id) DO NOTHING; \
               """

async def init_sqlite_db() -> None:
    logger.info("Initializing SQLite file and applying database schema...")
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.executescript(SQL_SCHEMA)
        await db.commit()
    logger.info("Database schema applied successfully.")

async def download_dataset_streamed(url: str, destination: pathlib.Path) -> None:
    if destination.exists():
        logger.info(f"Local cache found at {destination}. Skipping download.")
        return

    logger.info(f"Streaming dataset from Cloudflare R2: {url}...")
    destination.parent.mkdir(parents=True, exist_ok=True)

    timeout = httpx.Timeout(60.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("GET", url) as response:
            if response.status_code != 200:
                raise RuntimeError(f"R2 stream failed with status: {response.status_code}")

            with open(destination, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    f.write(chunk)

    logger.info(f"Dataset stored locally at: {destination}")

def map_row_to_domain(row: dict[str, Any]) -> Job:
    emp_type: EmploymentType = "FULL_TIME"
    raw_emp = row.get("employment_type")
    if raw_emp in ["FULL_TIME", "PART_TIME", "CONTRACT", "INTERN", "TEMPORARY"]:
        emp_type = raw_emp

    posted_at = None
    if row.get("posted_at"):
        try:
            posted_at = datetime.fromisoformat(str(row["posted_at"]).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass

    return Job(
        ats_type=str(row["ats_type"]).strip(),
        ats_id=str(row["ats_id"]).strip(),
        url=row["url"],
        apply_url=row["apply_url"] if row.get("apply_url") else None,
        title=str(row["title"]).strip(),
        company_slug=str(row["company"]).strip(),
        location=str(row["location"]).strip() if row.get("location") else None,
        is_remote=cast(IsRemote, evaluate_location_relevance(str(row["location"]).strip())),
        employment_type=emp_type,
        description=str(row["description"]) if row.get("description") else "",
        salary_min=float(row["salary_min"]) if row.get("salary_min") is not None else None,
        salary_max=float(row["salary_max"]) if row.get("salary_max") is not None else None,
        salary_currency=str(row["salary_currency"]).strip() if row.get("salary_currency") else None,
        application_questions=None,
        posted_at=posted_at
    )

def extract_job_tuple(job: Job) -> tuple[Any, ...]:
    return (
        job.ats_type,
        job.ats_id,
        str(job.url),
        str(job.apply_url) if job.apply_url else None,
        job.title,
        job.company_slug,
        job.location,
        job.employment_type,
        job.description,
        job.salary_min,
        job.salary_max,
        job.salary_currency,
        None,
        job.posted_at.isoformat() if job.posted_at else None,
        job.fetched_at.isoformat(),
        job.is_remote
    )

async def execute_batch_ingestion(parquet_path: pathlib.Path) -> None:
    logger.info("Opening memory-mapped view of the Parquet file...")
    lazy_engine = pl.scan_parquet(str(parquet_path))

    total_available_rows = lazy_engine.select(pl.len()).collect().item()
    logger.info(f"Total available rows in source file: {total_available_rows:,}")

    target_ingestion_limit = total_available_rows

    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute("PRAGMA journal_mode=WAL;")

        for offset in range(0, target_ingestion_limit, BATCH_SIZE):
            batch_df = lazy_engine.slice(offset, BATCH_SIZE).collect()

            # Map into a dictionary to preserve the structured domain objects for logging
            batch_map = {}
            for row_dict in batch_df.iter_rows(named=True):
                try:
                    domain_model = map_row_to_domain(row_dict)
                    key = (domain_model.ats_type, domain_model.ats_id)
                    batch_map[key] = domain_model
                except Exception as row_error:
                    logger.error(f"Error parsing row at offset {offset}: {row_error}")
                    continue

            if not batch_map:
                continue

            # Generate placeholders to check existing records in a single roundtrip
            keys_list = list(batch_map.keys())
            placeholders = ", ".join(["(?, ?)" for _ in keys_list])
            flattened_keys = [item for key in keys_list for item in key]

            check_query = f"""
                SELECT ats_type, ats_id 
                FROM jobs 
                WHERE (ats_type, ats_id) IN ({placeholders})
            """

            # Identify which items already exist in the database
            existing_keys = set()
            async with db.execute(check_query, flattened_keys) as cursor:
                async for row in cursor:
                    existing_keys.add((row[0], row[1]))

            # Split data into valid inserts and conflict logs
            insert_parameters = []
            for key, job in batch_map.items():
                if key in existing_keys:
                    logger.warning(
                        f"Conflict detected: Job {key[0]}:{key[1]} already exists. SQLite omitted entry."
                    )
                else:
                    insert_parameters.append(extract_job_tuple(job))

            if insert_parameters:
                await db.executemany(INSERT_QUERY, insert_parameters)
                await db.commit()

            processed_count = offset + batch_df.height
            progress_percentage = (processed_count / target_ingestion_limit) * 100

            skipped = len(existing_keys)
            inserted = len(insert_parameters)
            logger.info(
                f"Progress: {processed_count:,}/{target_ingestion_limit:,} ({progress_percentage:.2f}%) "
                f"| Batch: {inserted} inserted, {skipped} conflicts skipped."
            )

async def main() -> None:
    await init_sqlite_db()

    start_time = datetime.utcnow()
    try:
        await download_dataset_streamed(DATASET_URL, LOCAL_TMP_FILE)
        await execute_batch_ingestion(LOCAL_TMP_FILE)
    except Exception as e:
        logger.critical(f"Pipeline crashed: {e}", exc_info=True)
        sys.exit(1)
    finally:
        if LOCAL_TMP_FILE.exists():
            logger.info("Cleaning up temporary local Parquet storage...")
            LOCAL_TMP_FILE.unlink()

    duration = (datetime.utcnow() - start_time).total_seconds()
    logger.info(f"Done! Pipeline execution completed in {duration:.1f} seconds.")

if __name__ == "__main__":

    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
    asyncio.run(main())
