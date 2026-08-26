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
import html
import multiprocessing
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

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


def main() -> int:
    p = argparse.ArgumentParser(
        description="Normalize job descriptions in SQLite in-place."
    )
    p.add_argument("db_path", type=Path, help="Path to SQLite database file")
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

    if not args.db_path.exists():
        print(f"Error: Database file '{args.db_path}' not found.", file=sys.stderr)
        return 1

    # Connect and optimize SQLite pragmas
    conn = sqlite3.connect(args.db_path, isolation_level=None)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA cache_size = -64000;")  # 64MB cache

    # Validate table and columns
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({args.table})")
    columns = [row[1] for row in cursor.fetchall()]
    if not columns:
        print(f"Error: Table '{args.table}' not found.", file=sys.stderr)
        return 2
    if args.id_col not in columns:
        print(f"Error: ID column '{args.id_col}' not found.", file=sys.stderr)
        return 2
    if args.column not in columns:
        print(f"Error: Column '{args.column}' not found.", file=sys.stderr)
        return 2

    # Query only records that contain content
    select_query = f"SELECT {args.id_col}, {args.column} FROM {args.table} WHERE {args.column} IS NOT NULL AND is_normalized = 0"
    update_query = (
        f"UPDATE {args.table} SET {args.column} = ?, is_normalized = 1 WHERE {args.id_col} = ?"
    )

    read_cursor = conn.cursor()
    read_cursor.execute(select_query)

    print(
        f"Normalizing SQLite table '{args.table}' ({args.db_path}) "
        f"[-j {args.workers}, chunk={args.chunk}, column={args.column}]",
        flush=True,
    )

    t0 = time.time()
    counts = {"unchanged": 0, "shrunk": 0, "nulled": 0, "grew": 0, "newly_set": 0}
    total = 0

    pool = multiprocessing.Pool(args.workers)

    try:
        while True:
            chunk = read_cursor.fetchmany(args.chunk)
            if not chunk:
                break

            # Parallel sub-chunking
            n_workers = pool._processes
            sub_size = max(1, len(chunk) // n_workers + 1)
            sub_batches = [
                chunk[i : i + sub_size] for i in range(0, len(chunk), sub_size)
            ]

            results = pool.map(_normalize_batch, sub_batches)

            updates: List[Tuple[str, Any]] = []
            for sub in results:
                for row_id, old_desc, new_desc in sub:
                    total += 1
                    old_str = old_desc or ""
                    new_str = new_desc or ""

                    if new_str == old_str:
                        counts["unchanged"] += 1
                        continue

                    if not new_str:
                        counts["nulled"] += 1
                    elif not old_str:
                        counts["newly_set"] += 1
                    elif len(new_str) < len(old_str):
                        counts["shrunk"] += 1
                    else:
                        counts["grew"] += 1

                    updates.append((new_str, row_id))

            if updates:
                conn.execute("BEGIN IMMEDIATE")
                conn.executemany(update_query, updates)
                conn.execute("COMMIT")

            if total % (args.chunk * 5) == 0:
                elapsed = time.time() - t0
                rate = total / max(elapsed, 0.001)
                print(
                    f"  {total:,} rows processed · {rate:,.0f}/s · "
                    f"updated={total - counts['unchanged']:,} unchanged={counts['unchanged']:,}",
                    flush=True,
                )
    finally:
        pool.close()
        pool.join()
        conn.close()

    elapsed = time.time() - t0
    print(f"DONE total={total:,} in {elapsed:.1f}s · {counts}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())