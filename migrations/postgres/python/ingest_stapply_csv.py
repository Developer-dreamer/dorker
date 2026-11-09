#!/usr/bin/env python3
"""Download and batch-ingest jobs CSV datasets from data.stapply.ai into dorker_db.

Resolves `company_id` dynamically against the `companies` table in Postgres:
1. Matches companies by `UNIQUE(ats, name, slug, url)` (using Stapply's companies.csv
   metadata + local `companies` table) and falls back to disambiguated `(ats, slug)` /
   `(ats, name)` / job URL prefix / single-tenant ATS matching.
2. If no matching company exists in `companies`, assigns `company_id = -1`
   (referencing the sentinel unknown company row in `companies`).

Usage examples:
  # Stream a specific ATS dataset from data.stapply.ai (via manifest.json)
  python -m migrations.postgres.python.ingest_stapply_csv --ats ashby

  # Stream multiple ATS datasets from data.stapply.ai
  python -m migrations.postgres.python.ingest_stapply_csv --ats ashby,greenhouse,lever

  # Stream the full all.csv snapshot from data.stapply.ai
  python -m migrations.postgres.python.ingest_stapply_csv --all

  # Ingest from a direct CSV URL or local CSV file (e.g. stapply/data/ai.csv)
  python -m migrations.postgres.python.ingest_stapply_csv --file stapply/data/ai.csv

Run these commands from the repository root. Existing job IDs are skipped without
updates, including jobs marked deleted. Dry runs count candidates without checking
whether their IDs already exist in Postgres.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import os
import re
import sys
import time
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg
import httpx

# Increase CSV field size limit to handle large job descriptions & raw JSON blobs
csv.field_size_limit(sys.maxsize)

MANIFEST_URL = "https://storage.stapply.ai/jobhive/v1/manifest.json"
COMPANIES_CSV_URL = "https://storage.stapply.ai/jobhive/v1/companies.csv"
DEFAULT_PG_DSN = os.getenv(
    "DATABASE_URL", "postgresql://postgres:password@localhost:5432/dorker_db"
)
UNKNOWN_COMPANY_ID = -1

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("ingest_stapply_csv")


# --- Salary & Date Parsing Helpers -------------------------------------------

_SALARY_RANGE_RE = re.compile(
    r"""
    (?P<sym1>[$£€¥]|CA\$|US\$|A\$|NZ\$|HK\$|S\$|R\$)?\s*
    (?P<n1>\d[\d,. ]*)\s*
    (?P<u1>[KMkm]|thousand|million)?
    \s*(?:[-–—~]|to)\s*
    (?P<sym2>[$£€¥]|CA\$|US\$|A\$|NZ\$|HK\$|S\$|R\$)?\s*
    (?P<n2>\d[\d,. ]*)\s*
    (?P<u2>[KMkm]|thousand|million)?
    """,
    re.VERBOSE,
)
_SALARY_SINGLE_RE = re.compile(
    r"""
    (?P<sym>[$£€¥]|CA\$|US\$|A\$)?\s*
    (?P<n>\d[\d,. ]{2,})\s*
    (?P<u>[KMkm]|thousand|million)?
    """,
    re.VERBOSE,
)


def _parse_salary_token(num: str, unit: str | None) -> float | None:
    cleaned = num.replace(",", "").replace(" ", "").rstrip(".")
    if cleaned.count(".") > 1:
        cleaned = cleaned.replace(".", "", cleaned.count(".") - 1)
    try:
        value = float(cleaned)
    except ValueError:
        return None
    multiplier = 1.0
    if unit:
        u = unit.lower()
        if u.startswith("k") or u == "thousand":
            multiplier = 1_000.0
        elif u.startswith("m") or u == "million":
            multiplier = 1_000_000.0
    return value * multiplier


def parse_salary_range(text: str | None) -> tuple[float | None, float | None]:
    if not text or not text.strip():
        return (None, None)
    match = _SALARY_RANGE_RE.search(text)
    if match:
        lo = _parse_salary_token(match.group("n1"), match.group("u1"))
        hi = _parse_salary_token(match.group("n2"), match.group("u2"))
        if lo is not None and hi is not None and lo > hi:
            lo, hi = hi, lo
        return (lo, hi)
    match = _SALARY_SINGLE_RE.search(text)
    if match:
        val = _parse_salary_token(match.group("n"), match.group("u"))
        return (val, val)
    return (None, None)


def parse_float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def parse_dt(val: str | None) -> datetime | None:
    if not val:
        return None
    cleaned = val.strip()
    if not cleaned:
        return None
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(cleaned)
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"):
            try:
                return datetime.strptime(cleaned, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
    return None


def clean_str(val: str | None) -> str:
    if not val:
        return ""
    return val.replace("\x00", "").strip()


# --- Company Resolution Index ------------------------------------------------


def _compact_token(val: str) -> str:
    return re.sub(r"[\s\-_]+", "", val.lower())


@dataclass(frozen=True, slots=True)
class CompanyEntry:
    id: int
    ats: str
    name: str
    slug: str
    url: str
    url_norm: str


class CompanyResolver:
    """Resolves job rows to `companies.id` using `UNIQUE(ats, name, slug, url)`
    with fallback to `-1` (unknown company)."""

    def __init__(self) -> None:
        # Exact 4-tuple index matching UNIQUE(ats, name, slug, url)
        self.by_unique_4tuple: dict[tuple[str, str, str, str], int] = {}
        # Case-insensitive 4-tuple index
        self.by_unique_4tuple_ci: dict[tuple[str, str, str, str], int] = {}
        # Secondary indexes into dorker_db companies
        self.by_ats_slug: dict[tuple[str, str], list[CompanyEntry]] = {}
        self.by_ats_name: dict[tuple[str, str], list[CompanyEntry]] = {}
        self.by_ats_compact: dict[tuple[str, str], list[CompanyEntry]] = {}
        self.by_ats_url: dict[tuple[str, str], int] = {}
        self.by_ats_all: dict[str, list[CompanyEntry]] = {}
        # Mapping from Stapply's companies.csv: (ats, slug_or_name) -> list[(ats, name, slug, url)]
        self.stapply_meta_by_key: dict[tuple[str, str], list[tuple[str, str, str, str]]] = {}

    @staticmethod
    def _norm_url(url: str | None) -> str:
        if not url:
            return ""
        return url.strip().rstrip("/").lower()

    async def load_from_db(self, conn: asyncpg.Connection, *, dry_run: bool = False) -> None:
        if not dry_run:
            # The fallback FK is needed only when actually writing jobs.
            await conn.execute(
                """
                INSERT INTO companies (id, ats, slug, name, url, is_active)
                VALUES ($1, 'external', 'external', 'Unknown', 'https://example.com', FALSE)
                ON CONFLICT (id) DO NOTHING;
                """,
                UNKNOWN_COMPANY_ID,
            )

        rows = await conn.fetch(
            "SELECT id, ats, name, slug, url FROM companies WHERE id != $1;",
            UNKNOWN_COMPANY_ID,
        )
        for r in rows:
            cid: int = r["id"]
            ats: str = (r["ats"] or "").strip()
            name: str = (r["name"] or "").strip()
            slug: str = (r["slug"] or "").strip()
            url: str = (r["url"] or "").strip()
            url_norm = self._norm_url(url)

            entry = CompanyEntry(
                id=cid,
                ats=ats,
                name=name,
                slug=slug,
                url=url,
                url_norm=url_norm,
            )

            self.by_unique_4tuple[(ats, name, slug, url)] = cid
            self.by_unique_4tuple_ci[(ats.lower(), name.lower(), slug.lower(), url_norm)] = cid

            if slug:
                self.by_ats_slug.setdefault((ats.lower(), slug.lower()), []).append(entry)
                self.by_ats_compact.setdefault((ats.lower(), _compact_token(slug)), []).append(
                    entry
                )
            if name:
                self.by_ats_name.setdefault((ats.lower(), name.lower()), []).append(entry)
                self.by_ats_compact.setdefault((ats.lower(), _compact_token(name)), []).append(
                    entry
                )
            if url_norm:
                self.by_ats_url[(ats.lower(), url_norm)] = cid

            self.by_ats_all.setdefault(ats.lower(), []).append(entry)

        log.info(
            "Loaded %d companies from dorker_db (%d unique (ats, name, slug, url) keys).",
            len(rows),
            len(self.by_unique_4tuple),
        )

    async def load_stapply_companies_csv(self, client: httpx.AsyncClient) -> None:
        """Download storage.stapply.ai/jobhive/v1/companies.csv (ats,name,slug,url)
        so job rows (which only carry ats_type, company, and job url) can be mapped
        to their exact (ats, name, slug, url) 4-tuple before looking up in dorker_db."""
        try:
            log.info("Fetching Stapply companies metadata from %s ...", COMPANIES_CSV_URL)
            resp = await client.get(COMPANIES_CSV_URL, timeout=60.0)
            resp.raise_for_status()
            reader = csv.DictReader(resp.text.splitlines())
            count = 0
            for row in reader:
                ats = clean_str(row.get("ats"))
                name = clean_str(row.get("name"))
                slug = clean_str(row.get("slug"))
                url = clean_str(row.get("url"))
                if not ats or not slug:
                    continue
                quad = (ats, name, slug, url)
                self.stapply_meta_by_key.setdefault((ats.lower(), slug.lower()), []).append(quad)
                if name:
                    self.stapply_meta_by_key.setdefault((ats.lower(), name.lower()), []).append(
                        quad
                    )
                count += 1
            log.info("Loaded %d rows from Stapply companies.csv.", count)
        except Exception as exc:
            log.warning(
                "Could not load Stapply companies.csv (%s); proceeding with direct DB matching.",
                exc,
            )

    def resolve(self, row: dict[str, str]) -> int:
        ats = clean_str(row.get("ats_type") or row.get("ats")).lower()
        if not ats:
            return UNKNOWN_COMPANY_ID

        company_val = clean_str(row.get("company") or row.get("name"))
        slug_val = clean_str(row.get("slug"))
        company_url_val = clean_str(row.get("company_url"))
        job_url = clean_str(row.get("url"))
        job_url_norm = self._norm_url(job_url)

        # 1. Direct 4-tuple lookup if (ats, name, slug, url) are all present on row
        if company_val and slug_val and company_url_val:
            exact = self.by_unique_4tuple.get((ats, company_val, slug_val, company_url_val))
            if exact is not None:
                return exact
            ci = self.by_unique_4tuple_ci.get(
                (ats, company_val.lower(), slug_val.lower(), self._norm_url(company_url_val))
            )
            if ci is not None:
                return ci

        # 2. Resolve via Stapply's companies.csv (ats, name, slug, url) -> UNIQUE(ats, name, slug, url)
        if company_val:
            quads = self.stapply_meta_by_key.get((ats, company_val.lower()))
            if quads:
                if len(quads) == 1:
                    q_ats, q_name, q_slug, q_url = quads[0]
                    cid = self.by_unique_4tuple.get(
                        (q_ats, q_name, q_slug, q_url)
                    ) or self.by_unique_4tuple_ci.get(
                        (q_ats.lower(), q_name.lower(), q_slug.lower(), self._norm_url(q_url))
                    )
                    if cid is not None:
                        return cid
                else:
                    # Disambiguate multiple 4-tuples using job URL prefix
                    for q_ats, q_name, q_slug, q_url in quads:
                        q_url_norm = self._norm_url(q_url)
                        if q_url_norm and job_url_norm.startswith(q_url_norm):
                            cid = self.by_unique_4tuple.get(
                                (q_ats, q_name, q_slug, q_url)
                            ) or self.by_unique_4tuple_ci.get(
                                (q_ats.lower(), q_name.lower(), q_slug.lower(), q_url_norm)
                            )
                            if cid is not None:
                                return cid

        # 3. Match against dorker_db companies by (ats, slug), (ats, name), or compacted token
        if company_val:
            company_lower = company_val.lower()
            candidates = (
                self.by_ats_slug.get((ats, company_lower))
                or self.by_ats_name.get((ats, company_lower))
                or self.by_ats_compact.get((ats, _compact_token(company_val)))
            )
            if candidates:
                if len(candidates) == 1:
                    return candidates[0].id
                # Disambiguate using job URL prefix
                for cand in candidates:
                    if cand.url_norm and job_url_norm.startswith(cand.url_norm):
                        return cand.id
                return candidates[0].id

        # 4. Single-tenant / aggregator ATS in dorker_db (e.g. djinni, dou, amazon, apple, remoteok)
        ats_entries = self.by_ats_all.get(ats)
        if ats_entries and len(ats_entries) == 1:
            return ats_entries[0].id

        # 5. Fallback: unknown company id = -1
        return UNKNOWN_COMPANY_ID


# --- Streaming CSV Reader & Batch Ingestion ----------------------------------


def validate_csv_headers(headers: list[str]) -> None:
    missing = {"ats_id", "url", "title"} - set(headers)
    if not {"ats_type", "ats"}.intersection(headers):
        missing.add("ats_type (or ats)")
    if missing:
        raise ValueError(f"Jobs CSV is missing required columns: {', '.join(sorted(missing))}")


async def stream_csv_rows_from_url(
    client: httpx.AsyncClient, url: str
) -> AsyncGenerator[dict[str, str], None]:
    """Stream a remote CSV file line-by-line without loading the entire file into memory."""
    async with client.stream("GET", url, timeout=120.0, follow_redirects=True) as response:
        response.raise_for_status()
        headers: list[str] | None = None
        buffer = ""

        async for chunk in response.aiter_text():
            buffer += chunk
            while "\n" in buffer:
                # Use csv.reader on complete logical CSV rows; if quotes are unbalanced,
                # wait for more chunks before splitting.
                lines = buffer.splitlines(keepends=True)
                if not lines:
                    break

                # Accumulate lines until quote count is even (handles multiline descriptions)
                acc = ""
                quote_count = 0
                consumed_chars = 0
                found_complete_row = False

                for line in lines:
                    acc += line
                    consumed_chars += len(line)
                    quote_count += line.count('"')
                    if quote_count % 2 == 0 and line.endswith(("\n", "\r")):
                        found_complete_row = True
                        break

                if not found_complete_row:
                    # Need more data from network stream to close quoted multiline field
                    break

                buffer = buffer[consumed_chars:]
                parsed = next(csv.reader([acc]), None)
                if not parsed:
                    continue

                if headers is None:
                    headers = [h.strip().lstrip("\ufeff") for h in parsed]
                    validate_csv_headers(headers)
                else:
                    row_dict = {
                        headers[i]: (parsed[i] if i < len(parsed) else "")
                        for i in range(len(headers))
                    }
                    yield row_dict

        # Process any remaining data in buffer
        if buffer.strip():
            parsed = next(csv.reader([buffer]), None)
            if parsed and headers is None:
                validate_csv_headers([h.strip().lstrip("\ufeff") for h in parsed])
            elif parsed and headers is not None:
                row_dict = {
                    headers[i]: (parsed[i] if i < len(parsed) else "") for i in range(len(headers))
                }
                yield row_dict


async def stream_csv_rows_from_file(path: Path) -> AsyncGenerator[dict[str, str], None]:
    """Read a local CSV file row-by-row."""
    with open(path, mode="r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is not None:
            reader.fieldnames = [h.strip().lstrip("\ufeff") for h in reader.fieldnames]
            validate_csv_headers(reader.fieldnames)
        for row in reader:
            yield row


def transform_csv_row(row: dict[str, str], company_id: int) -> tuple[Any, ...] | None:
    """Transform a CSV row into the 18-column tuple expected by the `jobs` table."""
    ats_type = clean_str(row.get("ats_type") or row.get("ats")).lower()
    ats_id = clean_str(row.get("ats_id"))
    url = clean_str(row.get("url"))
    title = clean_str(row.get("title"))

    if not ats_type or not ats_id or not url or not title:
        return None

    job_id = f"{ats_type}:{ats_id}"
    apply_url = clean_str(row.get("apply_url")) or None
    location = clean_str(row.get("location")) or None
    country_iso = clean_str(row.get("country_iso"))[:2] or None
    region = clean_str(row.get("region")) or None
    employment_type = clean_str(row.get("employment_type")) or "FULL_TIME"
    description = clean_str(row.get("description"))

    salary_min = parse_float(row.get("salary_min"))
    salary_max = parse_float(row.get("salary_max"))
    if salary_min is None and salary_max is None:
        salary_summary = clean_str(row.get("salary_summary"))
        if salary_summary:
            salary_min, salary_max = parse_salary_range(salary_summary)

    salary_currency = clean_str(row.get("salary_currency"))[:3] or None
    posted_at = parse_dt(row.get("posted_at"))
    fetched_at = parse_dt(row.get("fetched_at") or row.get("date")) or datetime.now(timezone.utc)

    return (
        job_id,  # 0: id
        ats_type,  # 1: ats_type
        ats_id,  # 2: ats_id
        url,  # 3: url
        apply_url,  # 4: apply_url
        title,  # 5: title
        company_id,  # 6: company_id
        location,  # 7: location
        country_iso,  # 8: country_iso
        region,  # 9: region
        employment_type,  # 10: employment_type
        description,  # 11: description
        salary_min,  # 12: salary_min
        salary_max,  # 13: salary_max
        salary_currency,  # 14: salary_currency
        True,  # 15: is_normalized
        posted_at,  # 16: posted_at
        fetched_at,  # 17: fetched_at
    )


INSERT_JOBS_SQL = """
    INSERT INTO jobs (
        id, ats_type, ats_id, url, apply_url, title, company_id, location,
        country_iso, region, employment_type, description, salary_min,
        salary_max, salary_currency, is_normalized, posted_at, fetched_at
    )
    SELECT * FROM unnest(
        $1::text[],
        $2::text[],
        $3::text[],
        $4::text[],
        $5::text[],
        $6::text[],
        $7::int[],
        $8::text[],
        $9::varchar(2)[],
        $10::text[],
        $11::text[],
        $12::text[],
        $13::float8[],
        $14::float8[],
        $15::varchar(3)[],
        $16::bool[],
        $17::timestamptz[],
        $18::timestamptz[]
    )
    ON CONFLICT (id) DO NOTHING;
"""


async def flush_batch(
    conn: asyncpg.Connection,
    batch_by_id: dict[str, tuple[Any, ...]],
    *,
    dry_run: bool = False,
) -> int:
    """Insert new IDs and return PostgreSQL's actual inserted-row count."""
    if not batch_by_id:
        return 0
    records = list(batch_by_id.values())
    if dry_run:
        return 0

    columns = [[r[col_idx] for r in records] for col_idx in range(18)]
    status = await conn.execute(INSERT_JOBS_SQL, *columns)
    return int(status.rsplit(" ", 1)[-1])


@dataclass
class IngestStats:
    """Counters retained even if a later download or database batch fails."""

    read: int = 0
    valid: int = 0
    matched: int = 0
    unknown: int = 0
    skipped: int = 0
    candidates: int = 0
    inserted: int = 0


async def ingest_stream(
    source_label: str,
    row_stream: AsyncIterator[dict[str, str]],
    conn: asyncpg.Connection,
    resolver: CompanyResolver,
    *,
    batch_size: int = 2000,
    limit: int | None = None,
    dry_run: bool = False,
    stats: IngestStats | None = None,
) -> tuple[int, int, int]:
    """Consume `row_stream` in batches, resolve `company_id`, and insert new jobs.

    Returns `(total_valid, matched_companies, unknown_companies)`.
    """
    batch_by_id: dict[str, tuple[Any, ...]] = {}
    stats = stats if stats is not None else IngestStats()
    t0 = time.monotonic()
    last_progress = t0

    async for row in row_stream:
        stats.read += 1
        now = time.monotonic()
        if now - last_progress >= 10.0:
            log.info(
                "[%s] Read %s rows | Valid: %s | Skipped: %s | %s: %s",
                source_label, f"{stats.read:,}", f"{stats.valid:,}", f"{stats.skipped:,}",
                "Candidates (existence not checked)" if dry_run else "Inserted",
                f"{stats.candidates if dry_run else stats.inserted:,}",
            )
            last_progress = now

        company_id = resolver.resolve(row)
        record = transform_csv_row(row, company_id)
        if record is None:
            stats.skipped += 1
            if stats.skipped <= 3:
                missing = [
                    key for key in ("ats_id", "url", "title") if not clean_str(row.get(key))
                ]
                if not clean_str(row.get("ats_type") or row.get("ats")):
                    missing.append("ats_type (or ats)")
                log.warning(
                    "[%s] Skipping row %s: empty required fields: %s",
                    source_label, f"{stats.read:,}", ", ".join(missing),
                )
            continue

        if company_id == UNKNOWN_COMPANY_ID:
            stats.unknown += 1
        else:
            stats.matched += 1

        batch_by_id[record[0]] = record
        stats.valid += 1

        if len(batch_by_id) >= batch_size:
            log.info("[%s] Flushing %s jobs ...", source_label, f"{len(batch_by_id):,}")
            stats.inserted += await flush_batch(conn, batch_by_id, dry_run=dry_run)
            stats.candidates += len(batch_by_id)
            batch_by_id.clear()
            elapsed = max(0.001, time.monotonic() - t0)
            log.info(
                "[%s] Processed %s rows (%s matched company, %s -> id=-1, %.0f rows/s)",
                source_label,
                f"{stats.valid:,}",
                f"{stats.matched:,}",
                f"{stats.unknown:,}",
                stats.valid / elapsed,
            )
        if limit is not None and stats.valid >= limit:
            break

    if batch_by_id:
        log.info("[%s] Flushing %s jobs ...", source_label, f"{len(batch_by_id):,}")
        stats.inserted += await flush_batch(conn, batch_by_id, dry_run=dry_run)
        stats.candidates += len(batch_by_id)
        batch_by_id.clear()

    if stats.read and not stats.valid:
        raise ValueError(
            f"[{source_label}] All {stats.read:,} rows were rejected; "
            "check the required identity fields in the warnings above."
        )
    if not stats.read:
        log.warning("[%s] Source contains no job rows.", source_label)

    elapsed = max(0.001, time.monotonic() - t0)
    log.info(
        "✓ Finished [%s] in %.1fs: %s valid jobs (%s matched company [%.1f%%], %s unknown id=-1, %s skipped)",
        source_label,
        elapsed,
        f"{stats.valid:,}",
        f"{stats.matched:,}",
        (stats.matched / stats.valid * 100.0) if stats.valid else 0.0,
        f"{stats.unknown:,}",
        f"{stats.skipped:,}",
    )
    return stats.valid, stats.matched, stats.unknown


async def resolve_urls_from_manifest(
    client: httpx.AsyncClient,
    ats_filter: list[str] | None,
    use_all_csv: bool,
) -> list[tuple[str, str]]:
    """Fetch `manifest.json` from storage.stapply.ai and return `(label, csv_url)` pairs."""
    log.info("Fetching dataset manifest from %s ...", MANIFEST_URL)
    resp = await client.get(MANIFEST_URL, timeout=30.0)
    resp.raise_for_status()
    manifest = resp.json()

    if use_all_csv and not ats_filter:
        all_csv_url = manifest.get("all", {}).get("csv")
        if all_csv_url:
            return [("all.csv", all_csv_url)]

    by_ats: dict[str, Any] = manifest.get("by_ats", {})
    results: list[tuple[str, str]] = []

    target_ats = ats_filter if ats_filter else sorted(by_ats.keys())
    for ats_name in target_ats:
        ats_key = ats_name.strip().lower()
        entry = by_ats.get(ats_key)
        if not entry or not entry.get("csv"):
            log.warning("ATS '%s' not found or has no jobs CSV in manifest.json", ats_key)
            continue
        results.append((ats_key, entry["csv"]))

    if not results:
        raise ValueError("No jobs CSV sources matched the requested ATS selection.")
    return results


async def async_main(args: argparse.Namespace) -> int:
    log.info("Connecting to Postgres ...")
    conn = await asyncpg.connect(args.dsn)
    resolver = CompanyResolver()

    async with httpx.AsyncClient(headers={"User-Agent": "Dorker-StapplyIngest/1.0"}) as client:
        try:
            await resolver.load_from_db(conn, dry_run=args.dry_run)
            if not args.skip_stapply_companies_meta:
                await resolver.load_stapply_companies_csv(client)

            sources: list[tuple[str, AsyncGenerator[dict[str, str], None]]]

            if args.file:
                csv_path = Path(args.file).expanduser().resolve()
                if not csv_path.is_file():
                    raise FileNotFoundError(f"CSV file not found: {csv_path}")
                log.info("Ingesting local CSV file: %s", csv_path)
                sources = [(csv_path.name, stream_csv_rows_from_file(csv_path))]

            elif args.url:
                log.info("Streaming remote CSV URL: %s", args.url)
                sources = [(args.url, stream_csv_rows_from_url(client, args.url))]

            else:
                ats_list = (
                    [a.strip() for a in args.ats.split(",") if a.strip()] if args.ats else None
                )
                urls = await resolve_urls_from_manifest(client, ats_list, use_all_csv=args.all)
                sources = [
                    (label, stream_csv_rows_from_url(client, url)) for label, url in urls
                ]

            source_stats: list[IngestStats] = []
            failures = 0
            for label, rows in sources:
                stats = IngestStats()
                source_stats.append(stats)
                log.info("Starting [%s] ...", label)
                try:
                    async with aclosing(rows):
                        await ingest_stream(
                            label,
                            rows,
                            conn,
                            resolver,
                            batch_size=args.batch_size,
                            limit=args.limit,
                            dry_run=args.dry_run,
                            stats=stats,
                        )
                except Exception:
                    failures += 1
                    log.exception(
                        "Failed ingesting [%s]: read=%s, valid=%s, skipped=%s, %s=%s. "
                        "Earlier successful batches are retained.",
                        label, f"{stats.read:,}", f"{stats.valid:,}", f"{stats.skipped:,}",
                        "candidates (existence not checked)" if args.dry_run else "inserted",
                        f"{stats.candidates if args.dry_run else stats.inserted:,}",
                    )

            log.info(
                "=================================================================\n"
                "SUMMARY%s%s: Read: %s | Valid: %s | Skipped: %s | %s: %s | "
                "Existing skipped: %s | Failed sources: %s\n"
                "=================================================================",
                " (DRY RUN)" if args.dry_run else "",
                " (FAILED)" if failures else "",
                f"{sum(s.read for s in source_stats):,}",
                f"{sum(s.valid for s in source_stats):,}",
                f"{sum(s.skipped for s in source_stats):,}",
                "Candidates (existence not checked)" if args.dry_run else "Inserted",
                f"{sum(s.candidates if args.dry_run else s.inserted for s in source_stats):,}",
                "not checked" if args.dry_run else
                f"{sum(s.candidates - s.inserted for s in source_stats):,}",
                failures,
            )
            return 1 if failures else 0
        finally:
            await conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download jobs CSV from data.stapply.ai and insert new jobs into dorker_db."
    )
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument(
        "--ats",
        type=str,
        help="Comma-separated ATS names to pull from data.stapply.ai (e.g. 'ashby,greenhouse,lever').",
    )
    source_group.add_argument(
        "--all",
        action="store_true",
        help="Stream the combined all.csv dataset from data.stapply.ai.",
    )
    source_group.add_argument(
        "--url",
        type=str,
        help="Direct CSV URL to stream (e.g. https://storage.stapply.ai/jobhive/v1/ashby/jobs.csv).",
    )
    source_group.add_argument(
        "--file",
        type=str,
        help="Local CSV file path to ingest (e.g. stapply/data/ai.csv).",
    )
    parser.add_argument(
        "--dsn",
        type=str,
        default=DEFAULT_PG_DSN,
        help="Postgres connection string (defaults to DATABASE_URL or the local dorker_db).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=2000,
        help="Number of rows per Postgres UNNEST batch (default: 2000).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional max rows to ingest per CSV source (useful for testing).",
    )
    parser.add_argument(
        "--skip-stapply-companies-meta",
        action="store_true",
        help="Skip fetching storage.stapply.ai/jobhive/v1/companies.csv and match only via local DB columns.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and resolve company_id without writing to Postgres.",
    )
    args = parser.parse_args()
    if args.batch_size <= 0:
        parser.error("--batch-size must be greater than zero")
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be greater than zero")
    return args


if __name__ == "__main__":
    sys.exit(asyncio.run(async_main(parse_args())))
