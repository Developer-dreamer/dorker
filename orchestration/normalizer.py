#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import html
import multiprocessing
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from typing import Any

import asyncpg
from linkify_it import LinkifyIt
from linkify_it.tlds import TLDS
from markdownify import markdownify as md

_LINKIFY: LinkifyIt | None = None


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
        out = out[:start] + f"<{out[start:end]}>" + out[end:]
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
    if not s:
        return ""
    s = s.strip()
    if not s:
        return ""
    if HTML_BLOCK_RE.search(s):
        try:
            out = md(
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
        return autolink(out)
    if HTML_ANY_TAG_RE.search(s):
        out = re.sub(r"<[^>]+>", "", s)
        out = html.unescape(out)
        out = WS_RUN_RE.sub(" ", out).strip()
        return autolink(out)
    if HTML_ENTITY_RE.search(s):
        out = html.unescape(s).strip()
        return autolink(out)
    return autolink(s)


def _worker_process_batch(
    rows: list[tuple[Any, str | None]],
) -> tuple[list[tuple[Any, str]], list[Any]]:
    """Worker task: transforms batch and diffs in-worker to save IPC bandwidth."""
    updates: list[tuple[Any, str]] = []
    unchanged_ids: list[Any] = []

    for row_id, raw_desc in rows:
        normalized = normalize_one(raw_desc)
        old_val = raw_desc or ""
        if normalized == old_val:
            unchanged_ids.append(row_id)
        else:
            updates.append((row_id, normalized))

    return updates, unchanged_ids


PG_DSN = "postgresql://postgres:password@localhost:5432/dorker_db"


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Normalize job descriptions in PostgreSQL in-place."
    )
    parser.add_argument("--table", default="jobs")
    parser.add_argument("--id-col", default="id")
    parser.add_argument("--column", default="description")
    parser.add_argument(
        "-j",
        "--workers",
        type=int,
        default=max(1, multiprocessing.cpu_count() - 1),
    )
    parser.add_argument("--chunk", type=int, default=1000)
    args = parser.parse_args()

    pool = await asyncpg.create_pool(PG_DSN, min_size=4, max_size=10)

    try:
        # Determine ID data type for cast
        async with pool.acquire() as conn:
            id_type_row = await conn.fetchrow(
                """
                SELECT data_type, udt_name 
                FROM information_schema.columns 
                WHERE table_name = $1 AND column_name = $2
                """,
                args.table,
                args.id_col,
            )
            if not id_type_row:
                print(f"Column {args.id_col} not found.", file=sys.stderr)
                return 1

            id_type = id_type_row["udt_name"]
            # Cast common types correctly for asyncpg arrays
            if id_type in ("int4", "int8", "uuid", "text", "varchar"):
                id_cast = f"::{id_type}[]"
            else:
                id_cast = ""

        # Set-based updates using UNNEST
        bulk_update_sql = f"""
            UPDATE "{args.table}" AS t
            SET "{args.column}" = u.new_desc,
                is_normalized = TRUE
            FROM (
                SELECT unnest($1{id_cast}) AS id,
                       unnest($2::text[]) AS new_desc
            ) AS u
            WHERE t."{args.id_col}" = u.id
        """

        bulk_mark_sql = f"""
            UPDATE "{args.table}" AS t
            SET is_normalized = TRUE
            FROM (
                SELECT unnest($1{id_cast}) AS id
            ) AS u
            WHERE t."{args.id_col}" = u.id
        """

        raw_queue: asyncio.Queue[list[tuple[Any, str | None]] | None] = asyncio.Queue(maxsize=4)
        write_queue: asyncio.Queue[tuple[list[tuple[Any, str]], list[Any]] | None] = asyncio.Queue(
            maxsize=4
        )

        t0 = time.time()
        total_processed = 0

        async def reader() -> None:
            async with pool.acquire() as conn:
                # Keyset or isolated chunk cursor to prevent snapshot locks
                cursor_sql = f"""
                    SELECT "{args.id_col}", "{args.column}"
                    FROM "{args.table}"
                    WHERE is_normalized = FALSE AND "{args.column}" IS NOT NULL
                """
                async with conn.transaction():
                    cursor = await conn.cursor(cursor_sql)
                    while True:
                        rows = await cursor.fetch(args.chunk)
                        if not rows:
                            break
                        batch = [(r[args.id_col], r[args.column]) for r in rows]
                        await raw_queue.put(batch)
            await raw_queue.put(None)

        async def transformer(executor: ProcessPoolExecutor) -> None:
            loop = asyncio.get_running_loop()
            while True:
                batch = await raw_queue.get()
                if batch is None:
                    await write_queue.put(None)
                    raw_queue.task_done()
                    break

                # Sub-divide chunk across process pool
                sub_size = max(1, len(batch) // args.workers + 1)
                sub_batches = [batch[i : i + sub_size] for i in range(0, len(batch), sub_size)]

                tasks = [
                    loop.run_in_executor(executor, _worker_process_batch, sb) for sb in sub_batches
                ]
                results = await asyncio.gather(*tasks)

                combined_updates: list[tuple[Any, str]] = []
                combined_unchanged: list[Any] = []
                for updates, unchanged in results:
                    combined_updates.extend(updates)
                    combined_unchanged.extend(unchanged)

                await write_queue.put((combined_updates, combined_unchanged))
                raw_queue.task_done()

        async def writer() -> None:
            nonlocal total_processed
            async with pool.acquire() as conn:
                while True:
                    payload = await write_queue.get()
                    if payload is None:
                        write_queue.task_done()
                        break

                    updates, unchanged_ids = payload

                    if updates:
                        ids = [u[0] for u in updates]
                        texts = [u[1] for u in updates]
                        await conn.execute(bulk_update_sql, ids, texts)

                    if unchanged_ids:
                        await conn.execute(bulk_mark_sql, unchanged_ids)

                    total_processed += len(updates) + len(unchanged_ids)
                    write_queue.task_done()

                    elapsed = time.time() - t0
                    print(
                        f"Processed: {total_processed:,} | Rate: {total_processed / max(elapsed, 0.001):,.0f} rows/s",
                        end="\r",
                        flush=True,
                    )

        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            await asyncio.gather(
                reader(),
                transformer(executor),
                writer(),
            )

        # Mark NULL rows that were bypassed by cursor
        async with pool.acquire() as conn:
            await conn.execute(
                f'UPDATE "{args.table}" SET is_normalized = TRUE WHERE is_normalized = FALSE;'
            )

    finally:
        await pool.close()

    elapsed = time.time() - t0
    print(f"\nDone. Processed {total_processed:,} rows in {elapsed:.1f}s.")
    return 0


if __name__ == "__main__":
    asyncio.run(main())
