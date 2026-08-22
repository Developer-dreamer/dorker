import os
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

# isort: off
# ===== TIER 1 ====
from .scrapers import (
    AshbyScraper,
    GreenhouseScraper,
    LeverScraper,
    PersonioScraper,
    PinpointScraper,
    RecruiteeScraper,
    RipplingScraper,
    SmartRecruitersScraper,
    TeamtailorScraper,
    WorkableScraper
)

# ===== TIER 2 ====
from .scrapers import (
    BambooHRScraper,
    BreezyScraper,
    GemScraper,
    JazzHRScraper,
    JoinComScraper,
    MercorScraper,
    SoftgardenScraper
)

# ===== TIER 3 ====
from .scrapers import (
    ADPWorkforceNowScraper,
    AvatureScraper,
    CornerstoneScraper,
    DayforceScraper,
    iCIMSScraper,
    JobviteScraper,
    OracleScraper,
    PageUpScraper,
    PaycomScraper,
    PaylocityScraper,
    RecruiterboxScraper,
    SuccessFactorsScraper,
    TaleoScraper,
    UKGProScraper,
    WorkdayScraper
)

# ==== Single-tenant big-tech scrapers ====
from .scrapers import (
    AmazonScraper,
    AppleScraper,
    BytedanceScraper,
    GoogleScraper,
    MetaScraper,
    TeslaScraper,
    TikTokScraper,
    UberScraper
)

# ==== Temporarily ignored ====
# ==== Job aggregators ====
from .scrapers import (
    BuiltInScraper,
    RemoteOKScraper,
    TheHubScraper,
    WellfoundScraper,
    WeWorkRemotelyScraper,
    YCombinatorScraper,
    EightfoldScraper
)

from .scrapers import (
    BundesagenturScraper,
    ArbetsformedlingenScraper,
    EuresScraper
)
# isort: on


def _slug_col(row: dict[str, Any]) -> str | None:
    """Return the canonical ``slug`` column value if present and non-empty.

    Introduced by the 2026-05 ``ats-companies/`` migration: the CSV now
    carries an explicit ``slug`` column with the scraper/API identifier
    (decoupled from ``url`` which is the user-facing canonical URL).
    All slug-extractor helpers and lambdas below prefer this column
    first, falling back to the legacy url/name parsing logic so files
    that haven't been migrated yet still work.
    """
    slug = (row.get("slug") or "").strip()
    return slug or None


def _recruitee_slug(row: dict[str, Any]) -> str | None:
    """Recruitee tenants live at ``{slug}.recruitee.com``. CSVs sometimes
    store the human-readable name in the ``name`` column and the slug in
    the URL — always parse the URL when present."""
    url = (row.get("url") or "").strip()
    if (slug := _slug_col(row)):
        if url.startswith("http") and ".recruitee.com" not in urlparse(url).netloc:
            return url.rstrip("/")
        return slug.lower()
    if url.startswith("http"):
        m = re.match(r"https?://([a-z0-9][a-z0-9-]+)\.recruitee\.com", url, re.IGNORECASE)
        if m:
            return m.group(1).lower()
    name = (row.get("name") or "").strip()
    return name or None


def _personio_slug(row: dict[str, Any]) -> str | None:
    if (slug := _slug_col(row)):
        return slug.lower()
    url = (row.get("url") or "").strip()
    if url.startswith("http"):
        m = re.match(r"https?://([a-z0-9][a-z0-9-]+)\.jobs\.personio\.com",
                     url, re.IGNORECASE)
        if m:
            return m.group(1).lower()
    name = (row.get("name") or "").strip()
    return name or None


def _avature_slug(row: dict[str, Any]) -> str | None:
    """Avature tenants live at ``{slug}.avature.net`` (or sometimes the
    full careers URL). Extract the subdomain when a URL is present."""
    url = (row.get("url") or "").strip()
    if (slug := _slug_col(row)):
        if slug.startswith(("http://", "https://")):
            return slug
        if url.startswith("http"):
            base = _avature_base_from_url(url)
            if base is not None:
                return base
        return slug.lower()
    if url.startswith("http"):
        m = re.match(r"https?://([a-z0-9][a-z0-9-]+)\.avature\.net",
                     url, re.IGNORECASE)
        if m:
            return m.group(1).lower()
    name = (row.get("name") or "").strip()
    return name or None


def _avature_base_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    path = parsed.path.rstrip("/")
    if path == "/careers/SearchJobs":
        return None
    return f"{parsed.scheme}://{parsed.netloc}{path}".rstrip("/")


def _successfactors_slug(row: dict[str, Any]) -> str | None:
    """SuccessFactors tenants are best addressed by their careers host.

    The explicit ``slug`` column is useful for stable tenant identity, but it
    is not necessarily a resolvable host. After the 2026-05 CSV migration rows
    like ``slug=ace1950`` also carry ``url=https://ace1950.jobs2web.com``; the
    scraper needs the latter to avoid guessing ``job.ace1950.com``.
    """
    url = (row.get("url") or "").strip()
    if url:
        return url.rstrip("/")
    if (slug := _slug_col(row)):
        return slug
    return (row.get("name") or "").strip() or None


def _rippling_slug(row: dict[str, Any]) -> str | None:
    if (slug := _slug_col(row)):
        return slug.lower()
    url = (row.get("url") or "").strip()
    if url.startswith("http"):
        m = re.match(r"https?://ats\.rippling\.com/([a-z0-9][a-z0-9-]+)",
                     url, re.IGNORECASE)
        if m:
            return m.group(1).lower()
    name = (row.get("name") or "").strip()
    return name or None


def _workable_slug(row: dict[str, Any]) -> str | None:
    # Workable tenants are case-sensitive in the URL but lowercased
    # in the API; ``apply.workable.com`` is permissive. Preserve case
    # from the slug column when present.
    if (slug := _slug_col(row)):
        return slug
    url = (row.get("url") or "").strip()
    if url.startswith("http"):
        m = re.match(r"https?://apply\.workable\.com/([^/?#]+)",
                     url, re.IGNORECASE)
        if m:
            return m.group(1)
    name = (row.get("name") or "").strip()
    return name or None


def _lever_slug(row: dict[str, Any]) -> str | None:
    url = (row.get("url") or "").strip()
    if url.startswith("http"):
        m = re.match(r"https?://jobs\.lever\.co/([^/?#]+)", url, re.IGNORECASE)
        if m:
            return unquote(m.group(1))
    if (slug := _slug_col(row)):
        return slug
    name = (row.get("name") or "").strip()
    return name or None


def _greenhouse_slug(row: dict[str, Any]) -> str | None:
    if (slug := _slug_col(row)):
        return slug.lower()
    url = (row.get("url") or "").strip()
    if url.startswith("http"):
        m = re.match(
            r"https?://(?:job-boards|boards)\.greenhouse\.io/([^/?#]+)",
            url, re.IGNORECASE,
        )
        if m:
            return m.group(1).lower()
    name = (row.get("name") or "").strip()
    return name or None


def _ashby_slug(row: dict[str, Any]) -> str | None:
    if (slug := _slug_col(row)):
        return slug.lower()
    url = (row.get("url") or "").strip()
    if url.startswith("http"):
        m = re.match(r"https?://jobs\.ashbyhq\.com/([^/?#]+)", url, re.IGNORECASE)
        if m:
            return m.group(1).lower()
    name = (row.get("name") or "").strip()
    return name or None


def _oracle_slug(row: dict[str, Any]) -> str | None:
    """Return the Oracle API origin plus site selector.

    ``ats-companies/oracle.csv`` stores user-facing CandidateExperience URLs,
    e.g. ``https://host/hcmUI/CandidateExperience/en/sites/CX_1``. The scraper
    calls REST endpoints at the host root and needs the site number separately.
    """
    raw = (row.get("url") or "").strip()
    if not raw:
        return None
    if not raw.startswith(("http://", "https://")):
        return raw
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw
    base = f"{parsed.scheme}://{parsed.netloc}"
    site = _oracle_site_from_url(raw)
    return f"{base}?site_number={site}" if site else base


def _oracle_site_from_url(raw: str) -> str | None:
    parsed = urlparse(raw)
    query_site = parse_qs(parsed.query).get("site_number")
    if query_site and query_site[0]:
        return query_site[0]
    match = re.search(r"/sites/([^/?#]+)", parsed.path)
    if match:
        return match.group(1)
    return None


def _icims_slug(row: dict[str, Any]) -> str | None:
    """iCIMS rows can have a bare slug in `name`/`slug` or a
    `*.icims.com` URL. Either form is accepted by the scraper. Normalize
    standard `careers-` hosts to a slug, but preserve the full URL for every
    nonstandard prefix because that host cannot be reconstructed from a slug."""
    url = (row.get("url") or "").strip()
    if url:
        host = (urlparse(url).hostname or "").lower()
        m = re.fullmatch(r"careers-([a-z0-9-]+)\.icims\.com", host)
        if m:
            return m.group(1)
        if host.endswith(".icims.com"):
            return url.split("?", 1)[0].rstrip("/")
    if (slug := _slug_col(row)):
        return slug
    return (row.get("name") or "").strip() or None

# Per-ATS config:
# - scraper: the ats-scrapers class
# - slug: callable turning a CSV row into `company_slug`
# - kwargs (optional): callable returning additional kwargs for the scraper
#   (used by Phenom which needs `locale` and `country` per tenant)
# - csv: tenant CSV path (relative to repo root; canonical location
#   is ``ats-companies/{ats}.csv`` with columns ``name,url``)
# - output: jobs CSV output path (per-ATS jobs dataset under ``{ats}/``)

CONFIGS: dict[str, dict[str, Any]] = {
    "adp": {
        "scraper": ADPWorkforceNowScraper,
        "slug": lambda r: (r.get("url") or "").strip() or None,
        "kwargs": lambda r: {
            "company_name": (r.get("name") or "").strip() or None,
        },
        "defer_descriptions_to_cache": True,
        "description_cache_path": "adp/descriptions.sqlite3",
        "description_cache_compress": True,
        "max_concurrency": 1,
        "tenant_delay_seconds": 0.5,
        "description_concurrency": 1,
        "description_delay_seconds": 0.5,
        "fail_closed_on_any_error": True,
        "fail_closed_on_empty": True,
    },
    "cornerstone": {
        "scraper": CornerstoneScraper,
        # Scraper accepts either a bare slug OR the full career URL.
        # Prefer the slug column post-migration.
        "slug": lambda r: _slug_col(r) or r.get("url") or r.get("name"),
        "kwargs": lambda r: {
            "company_name": (r.get("name") or "").strip() or None,
        },
    },
    "icims": {
        "scraper": iCIMSScraper,
        "slug": _icims_slug,
        "kwargs": lambda r: {
            "company_name": (r.get("company_name") or "").strip() or None,
        },
        "dedupe_by_url": True,
    },
    "breezy": {
        "scraper": BreezyScraper,
        "slug": lambda r: _slug_col(r) or r.get("name"),
    },
    "gem": {
        # Gem boards live at ``jobs.gem.com/{slug}``. Post-migration the
        # slug column has the value directly; legacy files store it in the
        # last URL path component, which we extract as fallback.
        "scraper": GemScraper,
        "slug": lambda r: _slug_col(r) or (
            (r.get("url") or "").rstrip("/").rsplit("/", 1)[-1]
            if (r.get("url") or "").strip()
            else (r.get("name") or "").strip()
        ),
    },
    "successfactors": {
        "scraper": SuccessFactorsScraper,
        "slug": _successfactors_slug,
        "kwargs": lambda r: {
            "company_name": (r.get("name") or "").strip() or None,
        },
    },
    "taleo": {
        "scraper": TaleoScraper,
        # Taleo CSV stores bare URLs without scheme (the discovery flow
        # captures slug=full URL). The scraper needs `https://` prefix.
        "slug": lambda r: (
            (r.get("url") or "").strip()
            if (r.get("url") or "").startswith("http")
            else f"https://{(r.get('url') or '').strip()}"
            if r.get("url") else None
        ),
    },
    "oracle": {
        "scraper": OracleScraper,
        "slug": _oracle_slug,
        "kwargs": lambda r: {
            "company_name": (r.get("name") or "").strip() or None,
        },
        "dedupe_by_ats_id": True,
    },
    "pinpoint": {
        "scraper": PinpointScraper,
        "slug": lambda r: _slug_col(r) or r.get("name") or r.get("url"),
    },
    "recruiterbox": {
        "scraper": RecruiterboxScraper,
        "slug": lambda r: _slug_col(r) or r.get("name") or r.get("url"),
    },
    "workday": {
        # Workday's slug is the FULL careers URL (the scraper parses the
        # company/instance/site components). The CSV stores `url` directly.
        "scraper": WorkdayScraper,
        "slug": lambda r: (r.get("url") or "").strip() or None,
        "kwargs": lambda r: {
            "max_fetch_seconds": float(
                os.environ.get("ATS_SCRAPERS_WORKDAY_TENANT_TIMEOUT", "900")
            ),
            "company_name": (r.get("name") or "").strip() or None,
        },
        # Workday descriptions require per-job detail calls. Defer to the
        # disk-backed description cache so the scraper's internal enrichment
        # stays off (it would re-fetch every row, bypassing the cache).
        "defer_descriptions_to_cache": True,
        # Persistent zstd-compressed SQLite cache. ~700k entries from the
        # 2026-05 backfill seed the file; daily runs hit the cache for already
        # known URLs and only fetch the detail endpoint for new listings.
        "description_cache_path": "workday/descriptions.sqlite3",
        "description_cache_compress": True,
        # Workday can take longer than the publish window on bad API days. Keep
        # publishing the previous stable jobs.csv while a replacement is built.
        "publish_previous_while_running": True,
    },
    "bamboohr": {
        "scraper": BambooHRScraper,
        "slug": lambda r: _slug_col(r) or (r.get("name") or "").strip() or None,
    },
    "dayforce": {
        "scraper": DayforceScraper,
        "slug": lambda r: _slug_col(r) or (r.get("url") or "").strip() or None,
        "kwargs": lambda r: {"company_name": (r.get("name") or "").strip()},
        "fail_closed_on_any_error": True,
        "fail_closed_on_empty": True,
    },
    "teamtailor": {
        "scraper": TeamtailorScraper,
        "slug": lambda r: _slug_col(r) or (r.get("name") or "").strip() or None,
    },
    "ukg": {
        "scraper": UKGProScraper,
        "slug": lambda r: _slug_col(r) or (r.get("url") or "").strip() or None,
        "kwargs": lambda r: {"company_name": (r.get("name") or "").strip()},
        "fail_closed_on_any_error": True,
        "fail_closed_on_not_found": True,
        "fail_closed_on_empty": True,
    },
    "jazzhr": {
        "scraper": JazzHRScraper,
        # JazzHR sites are Cloudflare-protected — the scraper auto-falls
        # back to httpcloak under client_kind="auto".
        "slug": lambda r: _slug_col(r) or (r.get("name") or "").strip() or None,
    },
    "jobvite": {
        "scraper": JobviteScraper,
        "slug": lambda r: _slug_col(r) or (r.get("name") or "").strip() or None,
        "kwargs": lambda r: {"company_name": (r.get("name") or "").strip()},
        "fail_closed_on_any_error": True,
        "fail_closed_on_not_found": True,
        "fail_closed_on_empty": True,
    },
    "pageup": {
        "scraper": PageUpScraper,
        "slug": lambda r: _slug_col(r) or (r.get("name") or "").strip() or None,
        "kwargs": lambda r: {"company_name": (r.get("name") or "").strip()},
        "fail_closed_on_any_error": True,
        "fail_closed_on_empty": True,
    },
    "paycom": {
        "scraper": PaycomScraper,
        "slug": lambda r: _slug_col(r) or (r.get("url") or "").strip() or None,
        "kwargs": lambda r: {"company_name": (r.get("name") or "").strip()},
        "dedupe_by_ats_id": True,
        "max_concurrency": 3,
        "fail_closed_on_any_error": True,
        "fail_closed_on_empty": True,
    },
    "paylocity": {
        "scraper": PaylocityScraper,
        "slug": lambda r: _slug_col(r) or (r.get("url") or "").strip() or None,
        "kwargs": lambda r: {"company_name": (r.get("name") or "").strip()},
        "max_concurrency": 1,
        "fail_closed_on_any_error": True,
        "fail_closed_on_not_found": True,
        "fail_closed_on_empty": True,
    },
    "recruitee": {
        "scraper": RecruiteeScraper,
        # Recruitee CSVs mix human names ("5280 High School") with the
        # actual subdomain ("5280highschool"). Always derive the slug
        # from the URL when one is present.
        "slug": _recruitee_slug,
    },
    "ashby": {
        "scraper": AshbyScraper,
        "slug": _ashby_slug,
    },
    "lever": {
        "scraper": LeverScraper,
        "slug": _lever_slug,
    },
    "greenhouse": {
        "scraper": GreenhouseScraper,
        "slug": _greenhouse_slug,
    },
    "workable": {
        "scraper": WorkableScraper,
        "slug": _workable_slug,
    },
    "smartrecruiters": {
        "scraper": SmartRecruitersScraper,
        # SmartRecruiters slugs are case-sensitive (e.g. ``Dominos``,
        # not ``dominos``). Both the legacy ``name`` column and the new
        # ``slug`` column must preserve case — never call ``.lower()``
        # on them. If the slug column got lowercased by accident, fall
        # back to the name column which is canonically capitalized.
        "slug": lambda r: _slug_col(r) or (r.get("name") or "").strip() or None,
    },
    "softgarden": {
        "scraper": SoftgardenScraper,
        "slug": lambda r: _slug_col(r) or (r.get("url") or "").strip() or None,
        "dedupe_by_ats_id": True,
        "max_concurrency": 8,
        "fail_closed_on_empty": True,
        "fail_closed_on_any_error": True,
    },
    "personio": {
        "scraper": PersonioScraper,
        "slug": _personio_slug,
    },
    "rippling": {
        "scraper": RipplingScraper,
        "slug": _rippling_slug,
    },
    "avature": {
        "scraper": AvatureScraper,
        "slug": _avature_slug,
    },
    "join_com": {
        "scraper": JoinComScraper,
        # Prefer the canonical slug column; legacy fallback derives it
        # from the URL's last path component. The lowercased form
        # matters because names in the CSV come from the sitemap with
        # arbitrary casing and would 301-redirect on every probe.
        "slug": lambda r: (
            (_slug_col(r) or "").lower()
            or (r.get("url") or "").rstrip("/").rsplit("/", 1)[-1].lower()
            or None
        ),
    },
    "mercor": {
        "scraper": MercorScraper,
        # Mercor is a single-tenant scraper — slug is ignored.
        "slug": lambda r: "mercor",
    },
    # ---- Single-tenant big-tech scrapers (singleton mode) -----------------
    # Each big-tech employer runs its own bespoke careers system. These
    # scrapers ignore the slug (or use a fixed one) — wire them through
    # the runner via ``singleton: True`` so we don't need a one-row CSV
    # per company. Output goes to ``{ats}/jobs.csv``.
    "amazon": {
        "scraper": AmazonScraper, "singleton": True,
    },
    "apple": {
        "scraper": AppleScraper, "singleton": True,
    },
    "bytedance": {
        "scraper": BytedanceScraper, "singleton": True,
        "fail_closed_on_empty": True,
    },
    "google": {
        "scraper": GoogleScraper, "singleton": True,
    },
    "meta": {
        "scraper": MetaScraper, "singleton": True,
    },
    "tesla": {
        "scraper": TeslaScraper, "singleton": True,
    },
    "tiktok": {
        "scraper": TikTokScraper, "singleton": True,
    },
    "uber": {
        "scraper": UberScraper, "singleton": True,
    },
}

TEMPORARILY_EXCLUDED = {
    "bundesagentur": {
        # German federal employment agency — official, public, ~1M+ jobs.
        # Single-source aggregator; subdivides internally by berufsfeld
        # (job category) to bypass the 10k pagination cap.
        "scraper": BundesagenturScraper, "singleton": True,
        "output": "bundesagentur/jobs.csv",
    },
    "arbetsformedlingen": {
        # Sweden's federal employment service — public JSON API. ~46k
        # active jobs, fanned out across 21 regions to bypass the 10k cap.
        "scraper": ArbetsformedlingenScraper, "singleton": True,
        "output": "arbetsformedlingen/jobs.csv",
    },
    "eures": {
        # EU-wide aggregator across the 31 EURES countries. ~2.7M jobs;
        # subdivided per-country, then by NUTS region / NACE sector if a
        # country exceeds the 10k pagination cap.
        "scraper": EuresScraper, "singleton": True,
        "output": "eures/jobs.csv",
        # The pre-detail-fallback EURES CSV truncated descriptions at
        # 500 chars. Do not reuse that legacy file as a cache, otherwise
        # a full rerun would just preserve the truncated descriptions.
        "skip_description_cache_if_max_len_lte": 500,
    },

    "builtin": {
            # US tech jobs aggregator. ~3-6k live jobs depending on the day.
            "scraper": BuiltInScraper, "singleton": True,
            "output": "builtin/jobs.csv",
        },
        "remoteok": {
            # RemoteOK — remote-only listings, US-heavy. ~100 live.
            "scraper": RemoteOKScraper, "singleton": True,
            "output": "remoteok/jobs.csv",
        },
        "thehub": {
            # The Hub — Nordic startups, ships lat/lon. ~1k live.
            "scraper": TheHubScraper, "singleton": True,
            "output": "thehub/jobs.csv",
        },
        "wellfound": {
            # Wellfound (was AngelList Talent) — US startups. ~700 live;
            # opt-in Firecrawl path because the API is auth-gated.
            "scraper": WellfoundScraper, "singleton": True,
            "output": "wellfound/jobs.csv",
        },
        "weworkremotely": {
            # We Work Remotely — remote-only listings. ~500 live.
            "scraper": WeWorkRemotelyScraper, "singleton": True,
            "output": "weworkremotely/jobs.csv",
        },
        "ycombinator": {
            # Y Combinator's Work at a Startup board. ~770 live.
            "scraper": YCombinatorScraper, "singleton": True,
            "output": "ycombinator/jobs.csv",
        },
        "eightfold": {
            "scraper": EightfoldScraper,
            # CSV has either a slug (most tenants → ``{slug}.eightfold.ai``) or a
            # full custom-domain URL (``apply.careers.{co}.com``). Pass the slug
            # column verbatim; for full URLs we extract the slug and let the
            # ``base_url`` kwarg do the override.
            "slug": lambda r: (
                (r.get("slug") or r.get("url") or r.get("name") or "")
                .strip()
                .replace("https://", "")
                .replace("http://", "")
                .split("/")[0]
                .split(".")[0]
                or None
            ),
            "kwargs": lambda r: _eightfold_kwargs(r),
            "csv": "ats-companies/eightfold.csv",
            "output": "eightfold/jobs.csv",
        },
}


def _eightfold_kwargs(row: dict[str, Any]) -> dict[str, Any]:
    """Build Eightfold-specific overrides from a CSV row.

    - ``url`` (when full https://...) → ``base_url`` override (custom domains).
    - ``domain`` column → API ``domain`` parameter.
    - Otherwise default to ``{slug}.eightfold.ai`` and ``{slug}.com``.
    """
    kw: dict[str, Any] = {}
    raw_url = (row.get("url") or "").strip()
    if raw_url.startswith("http"):
        parsed = urlparse(raw_url)
        if parsed.scheme and parsed.netloc:
            kw["base_url"] = f"{parsed.scheme}://{parsed.netloc}"
        else:
            kw["base_url"] = raw_url.rstrip("/")
    domain = (row.get("domain") or "").strip()
    if domain:
        kw["domain"] = domain
    name = (row.get("name") or "").strip()
    if name:
        kw["company_name"] = name
    return kw
