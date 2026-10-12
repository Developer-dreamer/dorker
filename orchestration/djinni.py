"""One-run Djinni export; no database, telemetry, or scheduler.

    python -m orchestration.djinni --output cache/djinni-snapshot.json
    python -m orchestration.djinni --output cache/djinni-snapshot.json --resume

Activate the dorker Conda environment first. Default pacing is one listing request
per 10 seconds (about 72 minutes for 429 pages, plus response time). --max-pages 2
runs a small connectivity probe; it cannot report complete coverage. Use --resume
only after resolving a block, or to continue a deliberate pause. No automatic retries
are made. Exit codes: 0 complete traversal/count match, 2 partial/blocked, 130 interrupted.
The output JSON contains status, reason, page progress, counts and jobs keyed by Djinni ID.
Read the caveats in src.scraping.djinni_snapshot before treating this as a snapshot.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import logging
import math
from pathlib import Path

import httpx

from src.scraping.djinni_snapshot import USER_AGENT, Snapshot, collect, load_snapshot


async def run(output: Path, *, resume: bool, delay: float, max_pages: int | None) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    # Prevent two invocations from interleaving writes to the same checkpoint.
    with output.with_suffix(output.suffix + ".lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another collector holds this output file") from exc
        if output.exists() and not resume:
            raise ValueError("Output already exists; use --resume or choose a new output path")
        state = load_snapshot(output) if resume else Snapshot()
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html", "Accept-Language": "en"},
        ) as client:
            await collect(client, state, output, delay=delay, max_pages=max_pages)
        print(f"{state.status}: {len(state.jobs)} unique jobs; next page {state.next_page}")
        print(state.reason or "No completion reason recorded")
        print(f"Export: {output.resolve()}")
        return 0 if state.status == "complete" else 2


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--output", type=Path, default=Path("cache/djinni-snapshot.json"))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--delay", type=float, default=10.0)
    parser.add_argument("--max-pages", type=int)
    args = parser.parse_args()
    if (
        not math.isfinite(args.delay)
        or args.delay < 1
        or (args.max_pages is not None and args.max_pages < 1)
    ):
        parser.error("--delay must be >= 1 and --max-pages must be >= 1")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        return asyncio.run(
            run(args.output, resume=args.resume, delay=args.delay, max_pages=args.max_pages)
        )
    except KeyboardInterrupt:
        print("Interrupted; validated pages remain in the export. Use --resume to continue.")
        return 130
    except (ValueError, OSError) as exc:
        parser.exit(2, f"{exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
