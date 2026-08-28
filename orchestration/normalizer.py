#!/usr/bin/env python3
"""Normalize the description column in a SQLite database in place,
processing in parallel batches to bound memory and minimize write locks.

Workflow:
  - Open SQLite in WAL mode for concurrent read/write isolation
  - Fetch batches of (id, description)
  - Dispatch chunks to a multiprocessing pool for normalization
  - Write updated rows back in batched transactions (skipping unchanged rows)
"""
from __future__ import annotations

import argparse
import asyncio
import html
import multiprocessing
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Callable, List, Tuple

import asyncpg
from linkify_it import LinkifyIt
from linkify_it.tlds import TLDS
from markdownify import markdownify as md

_MD = None


def _md_lazy() -> Callable[[Any], str] | None:
    global _MD
    if _MD is None:
        _MD = md
    return _MD


_LINKIFY = None


def _linkify_lazy() -> LinkifyIt:
    global _LINKIFY
    if _LINKIFY is None:
        instance = LinkifyIt()
        instance.tlds(TLDS)
        _LINKIFY = instance
    return _LINKIFY


def autolink(text: str) -> str:
    if not text:
        return text
    linkify = _linkify_lazy()
    matches = linkify.match(text)
    if not matches:
        return text

    out = text
    for m in reversed(matches):
        start, end = m.index, m.last_index
        before = out[max(0, start - 2) : start]
        after = out[end : end + 2]
        if before.endswith("](") or before.endswith("<") or after.startswith(">"):
            continue
        if before.endswith("!"):
            continue
        original = out[start:end]
        replacement = f"<{original}>"
        out = out[:start] + replacement + out[end:]
    return out


HTML_BLOCK_RE = re.compile(
    r"<(?:p|div|ul|ol|li|h[1-6]|br|table|tr|td|a|strong|em|b|i|span|section|article|hr|blockquote)\b",
    re.IGNORECASE,
)
HTML_ANY_TAG_RE = re.compile(r"<[a-z][a-z0-9]*\b[^>]*>", re.IGNORECASE)
HTML_ENTITY_RE = re.compile(r"&(?:nbsp|amp|lt|gt|quot|#\d+|[a-z]{2,8});", re.IGNORECASE)
BLANK_RUN_RE = re.compile(r"\n{3,}")
WS_RUN_RE = re.compile(r"\s+")


def normalize_one(s: str | None) -> str:
    if s is None:
        return ""
    s = s.strip()
    if not s:
        return ""
    if HTML_BLOCK_RE.search(s):
        try:
            out = _md_lazy()(
                s,
                heading_style="ATX",
                strip=["script", "style"],
                bullets="-",
                escape_underscores=False,
                wrap=False,
            )
        except Exception:
            out = re.sub(r"<[^>]+>", "", s)
            out = html.unescape(out)
        out = BLANK_RUN_RE.sub("\n\n", out).strip()
        return autolink(out) or ""
    if HTML_ANY_TAG_RE.search(s):
        out = re.sub(r"<[^>]+>", "", s)
        out = html.unescape(out)
        out = WS_RUN_RE.sub(" ", out).strip()
        return autolink(out) or ""
    if HTML_ENTITY_RE.search(s):
        out = html.unescape(s).strip()
        return autolink(out) or ""
    return autolink(s) or ""


def _normalize_batch(
    rows: List[Tuple[Any, str | None]],
) -> List[Tuple[Any, str | None, str | None]]:
    """Worker task: transforms [(id, raw_desc), ...] into [(id, raw_desc, normalized_desc), ...]."""
    return [(row_id, raw_desc, normalize_one(raw_desc)) for row_id, raw_desc in rows]


def _positive_int(value: str) -> int:
    try:
        ivalue = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected integer, got {value!r}") from exc
    if ivalue < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1 (got {ivalue})")
    return ivalue


PG_DSN = "postgresql://postgres:password@localhost:5432/dorker_db"


async def main() -> int:
    p = argparse.ArgumentParser(
        description="Normalize job descriptions in PostgreSQL in-place."
    )
    p.add_argument(
        "--table", default="jobs", help="Target table name (default: jobs)"
    )
    p.add_argument(
        "--id-col", default="id", help="Primary key column name (default: id)"
    )
    p.add_argument(
        "--column",
        default="description",
        help="Target column to normalize (default: description)",
    )
    p.add_argument(
        "-j",
        "--workers",
        type=_positive_int,
        default=max(1, multiprocessing.cpu_count() - 1),
        help="Number of worker processes",
    )
    p.add_argument(
        "--chunk",
        type=_positive_int,
        default=2000,
        help="Batch size for DB reads/writes",
    )
    args = p.parse_args()

    pool = await asyncpg.create_pool(PG_DSN, min_size=2, max_size=5)

    try:
        async with pool.acquire() as conn:
            # Validate table and columns via PostgreSQL information_schema
            columns_records = await conn.fetch(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = $1 AND table_schema = 'public'
                """,
                args.table,
            )
            columns = {row["column_name"] for row in columns_records}

            if not columns:
                print(f"Error: Table '{args.table}' not found.", file=sys.stderr)
                return 2
            if args.id_col not in columns:
                print(f"Error: ID column '{args.id_col}' not found.", file=sys.stderr)
                return 2
            if args.column not in columns:
                print(f"Error: Column '{args.column}' not found.", file=sys.stderr)
                return 2
            if "is_normalized" not in columns:
                print("Error: Column 'is_normalized' not found.", file=sys.stderr)
                return 2

        select_query = f"""
            SELECT "{args.id_col}", "{args.column}"
            FROM "{args.table}"
            WHERE "{args.column}" IS NOT NULL AND is_normalized = FALSE
        """
        # Updates both modified and unmodified records so is_normalized becomes TRUE
        update_query = f"""
            UPDATE "{args.table}"
            SET "{args.column}" = $1, is_normalized = TRUE
            WHERE "{args.id_col}" = $2
        """
        mark_normalized_only_query = f"""
            UPDATE "{args.table}"
            SET is_normalized = TRUE
            WHERE "{args.id_col}" = $1
        """

        print(
            f"Normalizing PostgreSQL table '{args.table}' "
            f"[-j {args.workers}, chunk={args.chunk}, column={args.column}]",
            flush=True,
        )

        t0 = time.time()
        counts = {
            "unchanged": 0,
            "shrunk": 0,
            "nulled": 0,
            "grew": 0,
            "newly_set": 0,
        }
        total = 0

        loop = asyncio.get_running_loop()

        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            async with pool.acquire() as read_conn, pool.acquire() as write_conn:
                # Cursors in PostgreSQL require an open transaction block
                async with read_conn.transaction():
                    cursor = await read_conn.cursor(select_query)

                    while True:
                        records = await cursor.fetch(args.chunk)
                        if not records:
                            break

                        # Convert asyncpg.Record instances to tuples for fast inter-process pickling
                        chunk = [
                            (r[args.id_col], r[args.column]) for r in records
                        ]

                        # Parallel sub-chunking across worker processes
                        sub_size = max(1, len(chunk) // args.workers + 1)
                        sub_batches = [
                            chunk[i : i + sub_size]
                            for i in range(0, len(chunk), sub_size)
                        ]

                        tasks = [
                            loop.run_in_executor(
                                executor, _normalize_batch, batch
                            )
                            for batch in sub_batches
                        ]
                        results = await asyncio.gather(*tasks)

                        content_updates: list[tuple[str, Any]] = []
                        unchanged_ids: list[tuple[Any]] = []

                        for sub in results:
                            for row_id, old_desc, new_desc in sub:
                                total += 1
                                old_str = old_desc or ""
                                new_str = new_desc or ""

                                if new_str == old_str:
                                    counts["unchanged"] += 1
                                    # Must mark is_normalized = TRUE, otherwise the record
                                    # will be re-fetched indefinitely on subsequent runs.
                                    unchanged_ids.append((row_id,))
                                    continue

                                if not new_str:
                                    counts["nulled"] += 1
                                elif not old_str:
                                    counts["newly_set"] += 1
                                elif len(new_str) < len(old_str):
                                    counts["shrunk"] += 1
                                else:
                                    counts["grew"] += 1

                                content_updates.append((new_str, row_id))

                        # Batch update database
                        if content_updates:
                            await write_conn.executemany(
                                update_query, content_updates
                            )
                        if unchanged_ids:
                            await write_conn.executemany(
                                mark_normalized_only_query, unchanged_ids
                            )

                        if total % (args.chunk * 5) == 0:
                            elapsed = time.time() - t0
                            rate = total / max(elapsed, 0.001)
                            print(
                                f"  {total:,} rows processed · {rate:,.0f}/s · "
                                f"updated={total - counts['unchanged']:,} unchanged={counts['unchanged']:,}",
                                flush=True,
                            )
    finally:
        await pool.close()

    elapsed = time.time() - t0
    print(f"DONE total={total:,} in {elapsed:.1f}s · {counts}", flush=True)
    return 0


if __name__ == "__main__":
    asyncio.run(main())