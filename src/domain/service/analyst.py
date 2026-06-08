import re

from config.logger import Logger
from domain.model.job import Job, extract_metadata, extract_question
from domain.model.prompt import PromptStructure
from infra.ai.gemini import GeminiFlashClient


class JobAnalyst:

    def __init__(self, logger: Logger, ai_client: GeminiFlashClient,
                 prompt_template: PromptStructure,
                 profile: str):
        self.logger = logger
        self.ai_client = ai_client
        self.prompt_template = prompt_template
        self.user_profile = profile

    def _build_quick_match_prompt(self, job: Job | str) -> str:
        return ""

    def _build_core_prompt(self, job: Job | str) -> str:
        full_payload =  f"""{self.prompt_template.master_prompt_with_generation}

                            <candidate_context>
                            {self.user_profile}
                            </candidate_context>

                            <job_payload>
                            # Source: {"user" if isinstance(job, str) else "scraper"}
                            {{job}}
                            <job_payload>"""

        # Forming core template
        match job:
            case str(): # The job description comes as a raw text from bot
                full_payload = full_payload.format(job=job)
            case Job(): # The job description comes from scraped after parsing and normaliation
                job_payload = f"""<metadata>
                                {extract_metadata(job)}
                                </metadata>
                                <description>
                                {job.description}
                                </description>
                                <application_questions>
                                {extract_question(job)}
                                </application_questions>"""

                full_payload = full_payload.format(job=job_payload)

        return full_payload

    def evaluate_location_relevance(self, location_str: str) -> str:
        """
        Analyzes a location string based on a real-world dataset of 3.7M vacancies.
        Returns a status string used for downstream business logic filtering.
        """
        if not location_str:
            return "REJECT_EMPTY"

        # Convert to lowercase and strip redundant whitespace
        location = location_str.lower().strip()

        # 1. TARGETED LOCAL REMOTE (Priority #1)
        ukraine_markers = ['ukraine', 'kyiv', 'kiev', 'lviv', 'odessa', 'kharkiv', 'dnipro']
        if any(w in location for w in ukraine_markers) or re.search(r'\bua\b', location):
            return "KEEP_LOCAL"

        # 2. GLOBAL AND REGIONAL REMOTE (Priority #2)
        # Includes explicit timezone markers and common regional abbreviations
        global_markers = ['worldwide', 'global', 'anywhere', 'world wide', 'emea', 'europe', 'international', 'latam', 'apac', 'utc', 'amer']
        if any(w in location for w in global_markers) or re.search(r'\b(eu|world)\b', location):
            return "KEEP_GLOBAL"

        # 3. PURE / GENERIC REMOTE
        # Compiled from the top abstract remote naming conventions in the dataset
        generic_remotes = {
            'remote', 'remote job', 'remote position', 'remote location', 'any location / remote', 
            'remote/homebased', 'remote locations', '1 remote', 'homebased', 'fully remote', 
            'remotely based', 'remote office', '100% remote', 'field/remote', 'remote worker - wfh', 
            'remote home office', 'remote worker', 'work from home', 'wfh', 'remote; work from home'
        }
        if location in generic_remotes or location.replace(' ', '') == '100%remote':
            return "KEEP_PURE"

        # 4. US EXCLUSION FILTERS (Hard drop)
        # Targets top US metropolitan areas and explicit country indicators
        us_specific_words = [
            'united states', 'america', 'usa', 'u.s.', 'nationwide', 'any state', 'san francisco', 
            'austin', 'boston', 'chicago', 'seattle', 'atlanta', 'new york', 'los angeles', 'silicon valley',
            'denver', 'dallas', 'houston', 'miami', 'philadelphia', 'phoenix', 'portland', 'noram', 'us_remote'
        ]
        if any(w in location for w in us_specific_words) or re.search(r'\b(us|usa|u\.s)\b', location) or location.endswith('-united-states'):
            return "EXCLUDE_US"

        # Validates US 2-letter state codes with explicit word boundaries (e.g., "Remote - TX")
        # Matches case-sensitive raw string to avoid false positives with lowercase substrings
        state_codes = [
            'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA', 'HI', 'ID', 'IL', 'IN', 'IA', 
            'KS', 'KY', 'LA', 'ME', 'MD', 'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ', 
            'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX', 'UT', 'VT', 
            'VA', 'WA', 'WV', 'WI', 'WY', 'PR', 'DC'
        ]
        if re.search(r'\b(' + '|'.join(state_codes) + r')\b', location_str):
            return "EXCLUDE_US_STATE"

        # Full names of US states
        us_states_full = [
            'alabama', 'alaska', 'arizona', 'arkansas', 'california', 'colorado', 'connecticut', 'delaware', 
            'florida', 'georgia', 'hawaii', 'idaho', 'illinois', 'indiana', 'iowa', 'kansas', 'kentucky', 
            'louisiana', 'maine', 'maryland', 'massachusetts', 'michigan', 'minnesota', 'mississippi', 
            'missouri', 'montana', 'nebraska', 'nevada', 'new hampshire', 'new jersey', 'new mexico', 
            'new york', 'north carolina', 'north dakota', 'ohio', 'oklahoma', 'oregon', 'pennsylvania', 
            'rhode island', 'south carolina', 'south dakota', 'tennessee', 'texas', 'utah', 'vermont', 
            'virginia', 'washington', 'west virginia', 'wisconsin', 'wyoming'
        ]
        if any(w in location for w in us_states_full):
            return "EXCLUDE_US_STATE"

        # 5. OTHER COUNTRIES EXCLUSION FILTERS (Incompatible tax or legal jurisdictions)
        other_countries_and_hubs = [
            'brazil', 'mexico', 'canada', 'india', 'germany', 'deutschland', 'united kingdom', 'london', 'uk', 'gb',
            'spain', 'philippines', 'colombia', 'poland', 'ireland', 'france', 'australia', 'singapore', 'japan',
            'china', 'netherlands', 'sweden', 'switzerland', 'austria', 'belgium', 'denmark', 'norway', 'finland',
            'argentina', 'portugal', 'serbia', 'romania', 'turkey', 'türkiye', 'slovakia', 'hungary', 'costa rica',
            'chile', 'paris', 'ontario', 'toronto', 'vancouver', 'sydney', 'melbourne', 'berlin', 'munich', 'amsterdam',
            'pakistan', 'italy', 'taiwan', 'south korea', 'cyprus', 'bulgaria', 'vietnam', 'malaysia', 'armenia', 'indonesia',
            'bangalore', 'manila', 'münchen', 'hamburg', 'düsseldorf', 'são paulo', 'bogota', 'madrid', 'barcelona', 'south africa'
        ]
        if any(c in location for c in other_countries_and_hubs):
            return "EXCLUDE_OTHER_COUNTRY"

        # Cleans by ISO 2-letter country codes with word boundaries (e.g., "Remote, br")
        country_iso2 = ['br', 'mx', 'ca', 'in', 'de', 'uk', 'es', 'pl', 'fr', 'au', 'sg', 'ie', 'pt', 'tr', 'ro', 'rs', 'pk', 'it', 'tw', 'kr', 'cy', 'bg', 'vn', 'my', 'am', 'id', 'co', 'za', 'lk', 'cn', 'cz']
        if re.search(r'\b(' + '|'.join(country_iso2) + r')\b', location):
            return "EXCLUDE_OTHER_COUNTRY"

        # Cleans by ISO 3-letter country prefixes found in the dataset (e.g., "IND-Remote", "BRA-Remote")
        if re.search(r'\b(ind|bra|bgr|arg|deu|aus|can|usa|phl|gbr|fra|esp|mex|col|prt)\b', location):
            return "EXCLUDE_OTHER_COUNTRY"

        # Default fallback: if no hard exclusion markers are matched, pass as a potential pure remote role
        return "POTENTIAL_PURE"