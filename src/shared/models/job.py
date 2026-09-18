from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal, Any

from pydantic import BaseModel, ConfigDict, HttpUrl

EmploymentType = Literal["FULL_TIME", "PART_TIME", "CONTRACT", "INTERN", "TEMPORARY"]

class ATSType(StrEnum):
    """Known job-source identifiers.

    Most values identify an ATS or job source with a registered scraper.
    ``CUSTOM`` is reserved for user-defined sources. Hosted-manifest keys are
    strings so newly published sources do not break older clients.
    """

    ADP = "adp"
    ASHBY = "ashby"
    AVATURE = "avature"
    BEISEN = "beisen"
    BEISEN_LEGACY = "beisen_legacy"
    CORNERSTONE = "cornerstone"
    DARWINBOX = "darwinbox"
    DAYFORCE = "dayforce"
    EIGHTFOLD = "eightfold"
    GEM = "gem"
    GREENHOUSE = "greenhouse"
    GUPY = "gupy"
    HERP = "herp"
    HRMOS = "hrmos"
    ICIMS = "icims"
    JOBVITE = "jobvite"
    JOIN_COM = "join_com"
    KEKA = "keka"
    LEVER = "lever"
    MERCOR = "mercor"
    MOKA = "moka"
    ORACLE = "oracle"
    PAGEUP = "pageup"
    PAYCOM = "paycom"
    PAYLOCITY = "paylocity"
    PERSONIO = "personio"
    PHENOM = "phenom"
    PINPOINT = "pinpoint"
    RECRUITERBOX = "recruiterbox"
    RIPPLING = "rippling"
    SMARTRECRUITERS = "smartrecruiters"
    SOFTGARDEN = "softgarden"
    SUCCESSFACTORS = "successfactors"
    UKG = "ukg"
    WORKABLE = "workable"
    WORKDAY = "workday"
    # Big-tech custom careers systems (single-tenant, bespoke APIs)
    AMAZON = "amazon"
    APPLE = "apple"
    BYTEDANCE = "bytedance"
    GOOGLE = "google"
    META = "meta"
    TESLA = "tesla"
    TIKTOK = "tiktok"
    UBER = "uber"
    USAJOBS = "usajobs"
    # National public-sector job boards (single-source, single-tenant
    # scrapers — each is the entire country's jobs api)
    BUNDESAGENTUR = "bundesagentur"
    ARBETSFORMEDLINGEN = "arbetsformedlingen"
    EURES = "eures"
    # Hybrid jobboards (companies post directly, not aggregated)
    WELCOMETOTHEJUNGLE = "welcometothejungle"
    GETONBRD = "getonbrd"
    WANTED = "wanted"
    REMOTEOK = "remoteok"
    WEWORKREMOTELY = "weworkremotely"
    PROGRAMATHOR = "programathor"
    BUILTIN = "builtin"
    JOBSCH = "jobsch"
    JOBSCZ = "jobs_cz"
    MANFRED = "manfred"
    THEHUB = "thehub"
    YCOMBINATOR = "ycombinator"
    WELLFOUND = "wellfound"
    INFOJOBSES = "infojobs_es"
    JOBBANKCA = "jobbankca"
    SEEK = "seek"
    DOU = "dou"
    DJINNI = "djinni"
    # Additional multi-tenant ATSes (post-0.1)
    BAMBOOHR = "bamboohr"
    BREEZY = "breezy"
    JAZZHR = "jazzhr"
    RECRUITEE = "recruitee"
    TALEO = "taleo"
    TEAMTAILOR = "teamtailor"
    CUSTOM = "custom"

class Job(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    ats_type: ATSType
    ats_id: str | None

    url: HttpUrl
    apply_url: HttpUrl | None

    title: str
    company_id: int
    location: str | None
    country_iso: str | None
    region: str | None
    employment_type: EmploymentType | None
    description: str | None

    salary_min: float | None
    salary_max: float | None
    salary_currency: str | None

    is_normalized: bool = False

    posted_at: datetime | None
    fetched_at: datetime | None

    def job_to_db_params(self) -> tuple[Any, ...]:
        return (
            self.id,
            self.ats_type.value,
            self.ats_id,
            str(self.url),
            str(self.apply_url) if self.apply_url else None,
            self.title,
            self.company_id,
            self.location,
            self.country_iso,
            self.region,
            self.employment_type or "FULL_TIME",
            self.description or "",
            self.salary_min,
            self.salary_max,
            self.salary_currency,
            self.is_normalized,
            self.posted_at,
            self.fetched_at or datetime.now(timezone.utc),
        )
