#!/usr/bin/env python3
import asyncio
import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import asyncpg

# --- Configuration ---
SQLITE_DB_PATH = Path("/Users/serafym/Developer/dorker.space/dorker/app.db")
PG_DSN = "postgresql://postgres:password@localhost:5432/dorker_db"
CHUNK_SIZE = 25_000

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("migration")


# --- Data Transformation Helpers ---


def parse_dt(val: Any) -> datetime | None:
    if not val or not isinstance(val, str):
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


def parse_bool(val: Any, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, int):
        return val != 0
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "t", "yes")
    return default


def parse_json(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return json.dumps(val)
    if isinstance(val, str):
        cleaned = val.strip()
        if not cleaned:
            return None
        try:
            json.loads(cleaned)
            return cleaned
        except ValueError:
            return json.dumps(cleaned)
    return None


def parse_float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def parse_int(val: Any) -> int | None:
    if val is None or val == "":
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


# --- Table Migration Definitions ---


def transform_ats_row(row: dict[str, Any]) -> tuple:
    return (
        row.get("ats_name"),
        row.get("company_slug"),
        row.get("company_name"),
        row.get("url"),
        parse_int(row.get("tier")),
        parse_bool(row.get("is_active"), default=True),
        parse_dt(row.get("last_attempt_at")),
        parse_dt(row.get("last_success_at")),
        parse_int(row.get("last_scrape_duration_ms")),
        parse_int(row.get("consecutive_errors")) or 0,
        row.get("last_error_message"),
        parse_int(row.get("last_job_count")) or 0,
        parse_int(row.get("consecutive_zero_jobs")) or 0,
    )


ATS_COLUMNS = [
    "ats_name",
    "company_slug",
    "company_name",
    "url",
    "tier",
    "is_active",
    "last_attempt_at",
    "last_success_at",
    "last_scrape_duration_ms",
    "consecutive_errors",
    "last_error_message",
    "last_job_count",
    "consecutive_zero_jobs",
]


def transform_jobs_row(row: dict[str, Any]) -> tuple:
    fetched_at = parse_dt(row.get("fetched_at")) or datetime.now(timezone.utc)
    return (
        row.get("id"),
        row.get("ats_type"),
        row.get("ats_id"),
        row.get("url"),
        row.get("apply_url"),
        row.get("title"),
        row.get("company_slug"),
        row.get("location"),
        (row.get("country_iso") or "")[:2] or None,
        row.get("region"),
        row.get("employment_type") or "FULL_TIME",
        row.get("description") or "",
        parse_float(row.get("salary_min")),
        parse_float(row.get("salary_max")),
        (row.get("salary_currency") or "")[:3] or None,
        parse_bool(row.get("is_normalized"), default=False),
        parse_dt(row.get("posted_at")),
        fetched_at,
    )


JOBS_COLUMNS = [
    "id",
    "ats_type",
    "ats_id",
    "url",
    "apply_url",
    "title",
    "company_slug",
    "location",
    "country_iso",
    "region",
    "employment_type",
    "description",
    "salary_min",
    "salary_max",
    "salary_currency",
    "is_normalized",
    "posted_at",
    "fetched_at",
]


def transform_matches_row(row: dict[str, Any]) -> tuple:
    return (
        row.get("id"),
        row.get("job_id"),
        parse_bool(row.get("is_technical"), default=False),
        row.get("suitability_tier"),
        row.get("pipeline_status") or "PENDING",
        parse_float(row.get("technical_capability_score")) or 0.0,
        parse_float(row.get("strategic_value_score")) or 0.0,
        row.get("strategic_reason"),
        parse_json(row.get("analytics")),
        parse_dt(row.get("created_at")) or datetime.now(timezone.utc),
        parse_dt(row.get("updated_at")) or datetime.now(timezone.utc),
    )


MATCHES_COLUMNS = [
    "id",
    "job_id",
    "is_technical",
    "suitability_tier",
    "pipeline_status",
    "technical_capability_score",
    "strategic_value_score",
    "strategic_reason",
    "analytics",
    "created_at",
    "updated_at",
]


def transform_batches_row(row: dict[str, Any]) -> tuple:
    return (
        row.get("id"),
        row.get("endpoint"),
        row.get("input_file_id"),
        row.get("output_file_id"),
        row.get("error_file_id"),
        row.get("purpose"),
        row.get("status"),
        row.get("completion_window") or "24h",
        parse_int(row.get("total_requests")) or 0,
        parse_int(row.get("completed_requests")) or 0,
        parse_int(row.get("failed_requests")) or 0,
        parse_int(row.get("created_at")) or 0,
        parse_int(row.get("in_progress_at")),
        parse_int(row.get("expires_at")),
        parse_int(row.get("finalizing_at")),
        parse_int(row.get("completed_at")),
        parse_int(row.get("failed_at")),
        parse_int(row.get("cancelled_at")),
        parse_json(row.get("custom_metadata")),
        parse_int(row.get("last_polled_at")),
        parse_int(row.get("downloaded_at")),
        parse_int(row.get("processed_at")),
        row.get("error_message"),
    )


BATCHES_COLUMNS = [
    "id",
    "endpoint",
    "input_file_id",
    "output_file_id",
    "error_file_id",
    "purpose",
    "status",
    "completion_window",
    "total_requests",
    "completed_requests",
    "failed_requests",
    "created_at",
    "in_progress_at",
    "expires_at",
    "finalizing_at",
    "completed_at",
    "failed_at",
    "cancelled_at",
    "custom_metadata",
    "last_polled_at",
    "downloaded_at",
    "processed_at",
    "error_message",
]


def transform_batch_items_row(row: dict[str, Any]) -> tuple:
    return (
        row.get("id"),
        row.get("batch_id"),
        row.get("job_id"),
        row.get("match_id"),
        row.get("status") or "PENDING",
        parse_int(row.get("http_status_code")),
        row.get("error_code"),
        row.get("error_message"),
        parse_dt(row.get("created_at")) or datetime.now(timezone.utc),
        parse_dt(row.get("processed_at")),
    )


BATCH_ITEMS_COLUMNS = [
    "id",
    "batch_id",
    "job_id",
    "match_id",
    "status",
    "http_status_code",
    "error_code",
    "error_message",
    "created_at",
    "processed_at",
]


TABLE_REGISTRY = [
    ("ats", ATS_COLUMNS, transform_ats_row),
]


# --- Core Pipeline Runner ---

# Map table names to a tuple of column index tuples that form a unique constraint
TABLE_UNIQUE_KEYS: dict[str, list[tuple[int, ...]]] = {
    # ats columns: 0=ats_name, 1=company_slug
    # Ensure BOTH columns form the uniqueness composite lookup key
    "ats": [(0, 1)],
    # jobs columns: 0=id, 1=ats_type, 2=ats_id
    # Check both the primary key (id) and the unique composite key (ats_type, ats_id)
    "jobs": [(0,), (1, 2)],
    # matches columns: 0=id
    "matches": [(0,)],
    # openai_batches columns: 0=id
    "openai_batches": [(0,)],
    # openai_batch_items columns: 0=id
    "openai_batch_items": [(0,)],
}


async def migrate_table(
    sqlite_conn: sqlite3.Connection,
    pg_conn: asyncpg.Connection,
    table_name: str,
    columns: list[str],
    transform_fn: Callable[[dict[str, Any]], tuple],
) -> None:
    cursor = sqlite_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    )
    if not cursor.fetchone():
        log.warning(f"Table '{table_name}' does not exist in SQLite. Skipping.")
        return

    total_rows = sqlite_conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
    log.info(f"Migrating '{table_name}': {total_rows:,} rows to transfer...")

    if total_rows == 0:
        return

    cursor = sqlite_conn.execute(f"SELECT * FROM {table_name}")
    col_names = [description[0] for description in cursor.description]

    unique_rules = TABLE_UNIQUE_KEYS.get(table_name, [(0,)])
    seen_keys: list[set[Any]] = [set() for _ in unique_rules]

    migrated_count = 0
    skipped_duplicates = 0
    t0 = time.time()

    while True:
        raw_rows = cursor.fetchmany(CHUNK_SIZE)
        if not raw_rows:
            break

        buffer = []
        for r in raw_rows:
            row_dict = dict(zip(col_names, r))
            transformed = transform_fn(row_dict)

            # Check if record violates any unique constraint
            is_duplicate = False
            for idx, key_indices in enumerate(unique_rules):
                key = tuple(transformed[i] for i in key_indices)
                if key in seen_keys[idx]:
                    is_duplicate = True
                    break

            if is_duplicate:
                skipped_duplicates += 1
                continue

            # Register key in seen sets
            for idx, key_indices in enumerate(unique_rules):
                key = tuple(transformed[i] for i in key_indices)
                seen_keys[idx].add(key)

            buffer.append(transformed)

        if buffer:
            await pg_conn.copy_records_to_table(table_name, records=buffer, columns=columns)

        migrated_count += len(buffer)
        rate = migrated_count / max(1.0, (time.time() - t0))
        log.info(
            f"  [{table_name}] {migrated_count:,} / {total_rows:,} rows migrated (skipped {skipped_duplicates:,} dupes, {rate:,.0f} rows/s)"
        )

    log.info(
        f"✓ Completed '{table_name}' in {time.time() - t0:.1f}s ({skipped_duplicates:,} duplicates dropped)."
    )


async def main() -> None:
    if not SQLITE_DB_PATH.exists():
        raise FileNotFoundError(f"SQLite database not found at {SQLITE_DB_PATH}")

    log.info(f"Connecting to Postgres at {PG_DSN}...")
    pg_conn = await asyncpg.connect(PG_DSN)

    log.info(f"Connecting to SQLite at {SQLITE_DB_PATH}...")
    sqlite_uri = f"{SQLITE_DB_PATH.resolve().as_uri()}?mode=ro"
    sqlite_conn = sqlite3.connect(sqlite_uri, uri=True)

    try:
        overall_start = time.time()

        for table_name, columns, transform_fn in TABLE_REGISTRY:
            await migrate_table(sqlite_conn, pg_conn, table_name, columns, transform_fn)

        total_elapsed = time.time() - overall_start
        log.info(
            f"\n{'=' * 50}\nAll tables migrated successfully in {total_elapsed:.1f}s (~{total_elapsed / 60:.1f} min)\n{'=' * 50}"
        )

    finally:
        sqlite_conn.close()
        await pg_conn.close()


if __name__ == "__main__":
    asyncio.run(main())
