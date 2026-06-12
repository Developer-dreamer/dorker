import json
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, JsonValue

EmploymentType = Literal["FULL_TIME", "PART_TIME", "CONTRACT", "INTERN", "TEMPORARY"]
IsRemote = Literal["REJECT_EMPTY", "REJECT_NO_REMOTE_CONTEXT", "KEEP_GLOBAL", "EXCLUDE_US",
                   "REJECT_HYBRID_NON_LOCAL", "POTENTIAL_PURE","EXCLUDE_US_STATE",
                   "EXCLUDE_OTHER_COUNTRY", "KEEP_GLOBAL", "KEEP_LOCAL", "KEEP_PURE",]

class Job(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ats_type: str = Field(...,
        description="The target applicant tracking system platform (e.g., 'greenhouse', 'lever', 'ashby').")
    ats_id: str = Field(...,
        description="The unique, platform-specific identifier assigned to the posting by the source ATS.")
    url: HttpUrl = Field(...,
        description="The direct public career page URL. Used as the primary stable tracking link.")
    apply_url: HttpUrl | None = Field(
        default=None,
        description="The dedicated endpoint for submitting applications, if distinct from the posting page URL.")

    title: str = Field(
        ...,
        description="The unformatted, literal job title as listed by the employer."
    )
    company_slug: str = Field(
        ...,
        description="The normalized identifier of the hiring entity mapped across the system."
    )
    location: str | None = Field(
        default=None,
        description="The free-form raw location string extracted directly from the posting headers."
    )
    is_remote: IsRemote = Field(
        default="POTENTIAL_PURE",
        description=""
    )
    employment_type: EmploymentType | None = Field(
        default="FULL_TIME",
        description="The cross-ATS normalized employment category used for strict database filtering."
    )
    description: str = Field(
        ...,
        description="Clean, plain-text job description with all HTML/Markdown tags stripped for LLM consumption."
    )

    # --- Structured Compensation Architecture ---
    salary_min: float | None = Field(
        default=None,
        description="The evaluated lower bound of the base compensation range."
    )
    salary_max: float | None = Field(
        default=None,
        description="The evaluated upper bound of the base compensation range."
    )
    salary_currency: str | None = Field(
        default=None,
        description="The three-letter ISO 4217 currency code representing the compensation framework."
    )

    # --- Proprietary Application Funnel Enrichment ---
    application_questions: list[JsonValue] | None = Field(
        default=None,
        description="Structured dictionary representation of custom application form inputs required by the ATS."
    )

    # --- System Metrics & Lifecycle Timestamps ---
    posted_at: datetime | None = Field(
        default=None,
        description="The initial publication timestamp reported by the source ATS ecosystem (UTC)."
    )
    fetched_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="The exact system timestamp recording when the row was ingested into the platform database (UTC)."
    )

def extract_metadata(job: Job) -> str:
    """Extracts critical positioning and contextual metadata from a CompactJob instance

    to populate the LLM's initial verification and scoring gates.
    """
    # 1. Format structured compensation safely
    if job.salary_currency and (job.salary_min is not None or job.salary_max is not None):
        min_amt = f"{job.salary_min:,.0f}" if job.salary_min is not None else "Unspecified"
        max_amt = f"{job.salary_max:,.0f}" if job.salary_max is not None else "Unspecified"
        salary_str = f"{min_amt} - {max_amt} {job.salary_currency}"
    else:
        salary_str = "Not disclosed / Competitive"

    # 2. Map Pydantic attributes to uniform English labels for prompt parsing
    metadata_map = {
        "Title": job.title,
        "Company Name": job.company_slug,
        "Salary Range": salary_str,
        "Location": job.location or "Not specified",
        "Is Remote": "Yes" if job.is_remote else "No",
        "Employment Type": job.employment_type or "FULL_TIME",
        "Source URL": str(job.url),
        "Date Published": job.posted_at.isoformat() if job.posted_at else "Unknown",
    }

    lines = [f"{key}: {value}" for key, value in metadata_map.items()]
    return ",\n".join(lines)


def extract_questions(job: Job) -> str:
    """Extracts custom application form questions required by the ATS.

    Serializes the structured dictionary objects into clean, sequential strings
    for target LLM extraction and answering passes.
    """
    if not job.application_questions:
        return ""

    formatted_questions = []

    for index, q_dict in enumerate(job.application_questions, start=1):
        if not isinstance(q_dict, dict):
            continue

        # Extract the human-readable question text based on common ATS form payloads
        # (checks 'label', 'text', 'name', or falls back to standard key-value match)
        question_text = (
            q_dict.get("label") or
            q_dict.get("text") or
            q_dict.get("name") or
            q_dict.get("description")
        )

        # If the schema structure is entirely custom/nested, serialize the raw entity
        if not question_text:
            question_text = json.dumps(q_dict, ensure_ascii=False)

        # Capture requirement flags if surfaced by the platform scraper
        required_flag = " (Required)" if q_dict.get("required") or q_dict.get("is_required") else ""

        formatted_questions.append(f"Question {index}: {str(question_text).strip()}{required_flag}")

    return ",\n".join(formatted_questions)

def evaluate_location_relevance(location_str: str) -> str:
        """
        Analyzes a location string based on a real-world dataset of 3.7M vacancies.
        Rejects raw non-remote geographic data and filters localized exclusions.
        """
        if not location_str: # Some locations are undefined. Completely ignore them.
            return "REJECT_EMPTY"

        # 1. STANDARDIZATION
        location_raw = location_str.strip()
        location = location_raw.lower()

        # ==========================================
        # STEP 1: CONTEXT SECURITY GATE
        # If the location does not have any Remote keyword in it - skip it.
        # ==========================================
        remote_context_tokens = ['remote', 'wfh', 'home', 'anywhere', 'worldwide', 'world wide', 'global', 'emea', 'europe']
        if not any(token in location for token in remote_context_tokens):
            return "REJECT_NO_REMOTE_CONTEXT"

        # Here is edge case with timezones. Position might be remote, but you can't work at night.
        # So such keywords as "AMER" (North, Central, and South America), "LATAM" (Latine America),
        # "APAC" (Asia-Pacific) could be skipped
        global_markers = ['worldwide', 'global', 'world wide', 'emea', 'europe', 'international', 'utc']
        if any(re.search(r'\b' + re.escape(w) + r'\b', location) for w in global_markers) or re.search(r'\b(eu|world)\b', location):
            return "KEEP_GLOBAL"

        # ==========================================
        # STEP 2: HARD EXCLUSIONS (Negative Filters First)
        # ==========================================

        # A. US Specific Phrases & Cities
        us_specific_words = [
            'united states', 'america', 'usa', 'u.s.', 'nationwide', 'any state', 'san francisco',
            'sanfrancisco', 'sf', 'austin', 'boston', 'chicago', 'seattle', 'atlanta', 'new york',
            'nyc', 'los angeles', 'la', 'silicon valley', 'bay area', 'bayarea', 'socal',
            'southern california','denver', 'dallas', 'houston', 'miami', 'philadelphia',
            'phoenix', 'portland', 'noram', 'us_remote', 'columbus', 'leawood', 'dtla',
            'maryland', 'sunnyvale', 'redwood city', 'mclean', 'arlington', 'san diego'
        ]
        if any(w in location for w in us_specific_words) or re.search(r'\b(us|usa|u\.s|sf|nyc|la)\b', location) or location.endswith('-united-states'):
            return "EXCLUDE_US"

        # B. US Case-Sensitive 2-Letter State Codes
        state_codes = [
            'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA', 'HI', 'ID', 'IL', 'IN',
            'IA', 'KS', 'KY', 'LA', 'ME', 'MD', 'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV',
            'NH', 'NJ', 'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC', 'SD', 'TN',
            'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'PR', 'DC', 'OH'
        ]
        if re.search(r'\b(' + '|'.join(state_codes) + r')\b', location_raw):
            return "EXCLUDE_US_STATE"

        # C. Full US State Names
        us_states_full = [
            'alabama', 'alaska', 'arizona', 'arkansas', 'california', 'colorado', 'connecticut',
            'delaware', 'florida', 'georgia', 'hawaii', 'idaho', 'illinois', 'indiana',
            'iowa', 'kansas', 'kentucky', 'louisiana', 'maine', 'maryland', 'massachusetts',
            'michigan', 'minnesota', 'mississippi', 'missouri', 'montana', 'nebraska',
            'nevada', 'new hampshire', 'new jersey', 'new mexico', 'new york', 'north carolina',
            'north dakota', 'ohio', 'oklahoma', 'oregon', 'pennsylvania',
            'rhode island', 'south carolina', 'south dakota', 'tennessee',
            'texas', 'utah', 'vermont', 'virginia', 'washington', 'west virginia',
            'wisconsin', 'wyoming', 'socal', 'south california', 'bay area'
        ]
        if any(w in location for w in us_states_full):
            return "EXCLUDE_US_STATE"

        # D. Non-compatible International Hubs & Countries (Including common spelling variations)
        other_countries_and_hubs = [
            'brazil', 'brasil', 'mexico', 'canada', 'india', 'germany', 'deutschland',
            'united kingdom', 'london', 'uk', 'gb', 'spain', 'philippines', 'colombia',
            'poland', 'ireland', 'france', 'australia', 'singapore', 'japan',
            'china', 'netherlands', 'sweden', 'switzerland', 'austria', 'belgium',
            'denmark', 'norway', 'finland', 'argentina', 'portugal', 'serbia',
            'romania', 'turkey', 'türkiye', 'slovakia', 'hungary', 'costa rica',
            'chile', 'paris', 'ontario', 'toronto', 'vancouver', 'sydney',
            'melbourne', 'berlin', 'munich', 'amsterdam', 'pakistan', 'italy',
            'taiwan', 'south korea', 'cyprus', 'bulgaria', 'vietnam', 'malaysia',
            'armenia', 'indonesia', 'bangalore', 'manila', 'münchen', 'hamburg',
            'düsseldorf', 'são paulo', 'sao paulo', 'bogota', 'madrid', 'barcelona',
            'south africa', 'dubai', 'lyon', 'bangkok', 'thailand', 'abuja', 'nigeria',
            'seoul', 'derbyshire', 'guatemala', 'leeds', 'sharjah', 'ae', 'boisbriand',
            'taupo', 'home counties', 'manchester', 'birmingham', 'scotland', 'wales',
            'islamabad', 'lahore', 'stellenbosch', 'nürnberg', 'münster', 'kiel',
            'bremen', 'ravensburg', 'rankweil', 'reading', 'zagreb', 'pula', 'lisbon',
            'zurich', 'mannheim', 'villach', 'bonn', 'freiburg', 'cork', 'suhl',
            'essen', 'fulda', 'hannover', 'mönchengladbach', 'österreich', 'schweiz',
            'neuss', 'mainz', 'stuttgart', 'köln', 'halle', 'leipzig', 'heidenheim',
            'würzburg', 'kassel', 'gießen', 'ludwigshafen', 'schwandorf', 'hengersberg',
            'plattlingen', 'karlsruhe', 'gronau', 'bad hersfeld', 'ingolstadt',
            'duisburg', 'neumünster', 'celle', 'minden', 'wolfsbrug', 'göttingen',
            'trier', 'heilbronn', 'ulm', 'augsburg', 'kempten', 'garmisch', 'passau',
            'rosenheim', 'fürth', 'landshut', 'burg', 'stendal', 'colbitz',
            'schönebeck', 'zerbst', 'königsborn', 'zeppernick', 'lübars', 'magdeburg',
            'bernau', 'eberswalde', 'strausberg', 'velten', 'potsdam', 'fürstenwalde',
            'bremerhaven', 'kaiserslautern', 'saarbrücken', 'salzburg', 'kufstein',
            'gelsenkirchen', 'rendsburg', 'aschaffenburg', 'meiningen', 'koblenz',
            'gera', 'aichstetten', 'allgäu', 'lindau', 'stendell', 'gramzow',
            'caselow', 'randowtal', 'schwedt', 'temmen', 'ringenwalde', 'gerswalde',
            'joachimsthal', 'prenzlau', 'lunow', 'stolzenhagen', 'görlitz', 'pinnow',
            'penkun', 'tantow', 'angermünde', 'horka', 'dresden', 'chemnitz',
            'dortmund', 'salzgitter', 'iserlohn', 'aachen', 'new delhi', 'tokyo',
            'taipei', 'schleswig-holstein', 'tpg zentrale', 'markkleeberg'
        ]
        if any(c in location for c in other_countries_and_hubs):
            return "EXCLUDE_OTHER_COUNTRY"

        # E. ISO 2-letter Country Codes
        country_iso2 = ['br', 'mx', 'ca', 'in', 'de', 'uk', 'es', 'pl', 'fr', 'au', 'sg', 'ie',
                        'pt', 'tr', 'ro', 'rs', 'pk', 'it', 'tw', 'kr', 'cy', 'bg', 'vn', 'my',
                        'am', 'id', 'co', 'za', 'lk', 'cn', 'cz', 'th', 'ae', 'ng', 'gt']
        if re.search(r'\b(' + '|'.join(country_iso2) + r')\b', location):
            return "EXCLUDE_OTHER_COUNTRY"

        # F. ISO 3-letter Country Prefixes
        if re.search(r'\b(ind|bra|bgr|arg|deu|aus|can|usa|phl|gbr|fra|esp|mex|col|prt|tha|are|nga|gtm)\b', location):
            return "EXCLUDE_OTHER_COUNTRY"

        # ==========================================
        # STEP 3: INCLUSIONS
        # This step is a soft gate: some job openings way go through it, even if
        # they are not suitable. However there is no way to reduce False-Negatives
        # to exactly zero.
        # ==========================================

        # This anywhere check here, to reject all jobs with location
        # "Anywhere within/from 'country name'"
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
