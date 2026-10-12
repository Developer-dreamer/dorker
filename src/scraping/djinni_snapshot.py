"""Resumable, HTTP-first Djinni listing export (independent of scheduled scraping).

Accepted approach: one paced session, public listings, local progress, stop on challenge.
The listing embeds descriptions, avoiding a request per job. No browser dependency,
challenge retries, or database side effects. The Djinni adapter delegates to this collector.
This module lives outside scrapers/ so upstream adapter synchronization cannot replace it.

A completed traversal with matching advertised counts is not an atomic site snapshot:
Djinni's offset pagination can shift while jobs are posted, edited, or removed. A resumed
run has the same limitation, amplified by the time between invocations. Start a new output
file to collect a fresh snapshot. Checkpoint and export are the same atomic JSON document;
its jobs are usable even when status is blocked/incomplete, but never imply full coverage.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, ValidationError

from src.scraping.models import Job
from src.shared.models.job import ATSType

BASE_URL = "https://djinni.co/jobs/"
USER_AGENT = "Dorker-DjinniSnapshot/1.0"
log = logging.getLogger(__name__)


class SnapshotError(Exception):
    """Response cannot establish listing coverage."""


class BlockedError(SnapshotError):
    """Challenge, rate limit, or access denial; do not retry automatically."""


class ListingPage(BaseModel):
    number: int
    total: int
    last_page: int
    jobs: list[Job]


class Snapshot(BaseModel):
    version: Literal[1] = 1
    source: Literal["https://djinni.co/jobs/"] = "https://djinni.co/jobs/"
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: Literal["running", "paused", "blocked", "incomplete", "complete"] = "running"
    reason: str | None = None
    next_page: int = 1
    initial_total: int | None = None
    latest_total: int | None = None
    count_changed: bool = False
    traversal_finished: bool = False
    resumed: bool = False
    page_ids: list[list[str]] = Field(default_factory=list)
    jobs: dict[str, Job] = Field(default_factory=dict)

    def save(self, path: Path | None) -> None:
        if path is None:
            return
        self.updated_at = datetime.now(UTC)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)


def check_response(response: httpx.Response) -> None:
    """Status alone is insufficient: a challenge can be an HTTP 200."""
    if response.headers.get("cf-mitigated", "").lower() == "challenge":
        raise BlockedError("Cloudflare challenge (cf-mitigated: challenge)")
    if response.status_code in {401, 403, 406, 429}:
        retry_after = response.headers.get("retry-after", "not supplied")
        raise BlockedError(f"HTTP {response.status_code}; Retry-After: {retry_after}")
    if response.status_code != 200:
        raise SnapshotError(f"HTTP {response.status_code}; no automatic retry")
    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
    if (
        any(marker in title for marker in ("just a moment", "attention required", "captcha"))
        or soup.select_one('#challenge-form, #cf-challenge-running, form[action*="captcha"]')
        or "window._cf_chl_opt" in response.text
    ):
        raise BlockedError("Challenge/CAPTCHA page received, including possible HTTP 200")


def parse_listing(body: str, expected_page: int) -> ListingPage:
    soup = BeautifulSoup(body, "html.parser")
    heading = soup.select_one("h1")
    count = heading.find_next_sibling("span") if heading else None
    count_text = re.sub(r"\s+", "", count.get_text() if count else "")
    if not count_text.isdecimal():
        raise SnapshotError("Missing advertised job count; unexpected page or challenge")
    total = int(count_text)
    active = soup.select_one('.pagination [aria-current="page"]')
    number = active.get_text(strip=True) if active else "1"
    if number != str(expected_page):
        raise SnapshotError(f"Requested page {expected_page}, received active page {number}")
    page_numbers = {expected_page}
    for anchor in soup.select(".pagination a[href]"):
        href = str(anchor.get("href"))
        if href == "#":
            continue
        parsed = urlparse(urljoin(BASE_URL, href))
        query = parse_qs(parsed.query)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "djinni.co"
            or parsed.path != "/jobs/"
            or set(query) != {"page"}
            or len(query["page"]) != 1
            or not query["page"][0].isdecimal()
        ):
            raise SnapshotError("Unexpected pagination URL")
        page_numbers.add(int(query["page"][0]))
    last_page = max(page_numbers)
    if last_page > expected_page and expected_page + 1 not in page_numbers:
        raise SnapshotError("Pagination skips the next page")

    jobs: list[Job] = []
    for card in soup.select("#jobs_main .job-item"):
        link = card.select_one("a.job_item__header-link[href]")
        title = card.select_one(".job-item__position")
        description = card.select_one(".js-original-text")
        company = card.select_one("header .small")
        location = card.select_one(".location-text")
        url = urljoin(BASE_URL, str(link.get("href"))) if link else ""
        match = re.fullmatch(r"https://djinni\.co/jobs/(\d+)-[^/?#]+/", url)
        if (
            not match
            or not title
            or not title.get_text(strip=True)
            or not description
            or not description.get_text(strip=True)
        ):
            raise SnapshotError("Malformed job card or missing full description")
        ats_id = match.group(1)
        if card.get("id") != f"job-item-{ats_id}":
            raise SnapshotError("Job card ID does not match its URL")
        jobs.append(
            Job(
                url=url,  # type: ignore[arg-type]
                title=title.get_text(" ", strip=True),
                company=company.get_text(" ", strip=True) if company else "Unknown",
                ats_type=ATSType.DJINNI,
                ats_id=ats_id,
                location=location.get_text(" ", strip=True) if location else None,
                description=description.decode_contents(),
                fetched_at=datetime.now(UTC),
            )
        )
    if total == 0 or not jobs:
        # The observed site is nonempty; require review before treating emptiness as success.
        raise SnapshotError("Empty listing cannot establish full coverage")
    ids = [job.ats_id for job in jobs]
    if len(set(ids)) != len(ids):
        raise SnapshotError("Duplicate job cards within a page")
    if len(jobs) < total and active is None:
        raise SnapshotError("Missing pagination on a multi-page listing")
    return ListingPage(number=expected_page, total=total, last_page=last_page, jobs=jobs)


async def collect(
    client: httpx.AsyncClient,
    state: Snapshot,
    output: Path | None,
    *,
    delay: float = 10.0,
    max_pages: int | None = None,
) -> Snapshot:
    """Fetch sequential pages; persist after each validated page and every controlled stop.

    max_pages limits requests in this invocation, not the advertised traversal.
    A limit always produces paused status unless the final page was reached.
    """
    if not math.isfinite(delay) or delay < 1 or (max_pages is not None and max_pages < 1):
        raise ValueError("delay must be >= 1 second and max_pages must be >= 1")
    if state.status == "complete" or state.traversal_finished:
        return state
    state.status, state.reason = "running", None
    state.save(output)
    try:
        robots_response = await client.get("https://djinni.co/robots.txt")
        check_response(robots_response)
        if "<html" in robots_response.text.lower():
            raise SnapshotError("robots.txt returned HTML")
        robots = RobotFileParser()
        robots.parse(robots_response.text.splitlines())
        crawl_delay = robots.crawl_delay(USER_AGENT)
        delay = max(delay, float(crawl_delay or 0))
        rate = robots.request_rate(USER_AGENT)
        if rate:
            delay = max(delay, rate.seconds / rate.requests)
        pages_this_run = 0
        while True:
            number = state.next_page
            url = BASE_URL if number == 1 else f"{BASE_URL}?page={number}"
            if not robots.can_fetch(USER_AGENT, url):
                raise BlockedError(f"robots.txt disallows page {number}")
            await asyncio.sleep(delay)
            response = await client.get(url)
            check_response(response)
            page = parse_listing(response.text, number)
            ids = [str(job.ats_id) for job in page.jobs]
            if any(set(ids) == set(previous) for previous in state.page_ids):
                raise SnapshotError(f"Repeated listing page at page {number}")
            if all(job_id in state.jobs for job_id in ids):
                raise SnapshotError(f"Page {number} added no new jobs")
            if state.initial_total is None:
                state.initial_total = page.total
            state.latest_total = page.total
            state.count_changed |= page.total != state.initial_total
            state.jobs.update({str(job.ats_id): job for job in page.jobs})
            state.page_ids.append(ids)
            state.next_page += 1
            pages_this_run += 1
            log.info(
                "Page %s/%s: %s unique jobs, advertised %s",
                number,
                page.last_page,
                len(state.jobs),
                page.total,
            )
            if number == page.last_page:
                state.traversal_finished = True
                if not state.count_changed and len(state.jobs) == page.total:
                    state.status = "complete"
                    state.reason = (
                        "Traversed all pages; unique count matches stable advertised total"
                    )
                else:
                    state.status = "incomplete"
                    state.reason = (
                        f"Traversal ended but coverage uncertain: {len(state.jobs)} unique, "
                        f"initial total {state.initial_total}, final total {page.total}, "
                        f"count changed={state.count_changed}. Start a fresh export to recheck."
                    )
            elif max_pages is not None and pages_this_run >= max_pages:
                state.status, state.reason = "paused", "Requested page limit reached"
            state.save(output)
            if state.status != "running":
                return state
    except (SnapshotError, httpx.HTTPError, ValidationError) as exc:
        state.status = "blocked" if isinstance(exc, BlockedError) else "incomplete"
        state.reason = f"Page {state.next_page}: {exc}"
        state.save(output)
    except asyncio.CancelledError:
        state.status, state.reason = "paused", "Interrupted; last validated page saved"
        state.save(output)
        raise
    return state


def load_snapshot(path: Path) -> Snapshot:
    state = Snapshot.model_validate(json.loads(path.read_text(encoding="utf-8")))
    if state.next_page != len(state.page_ids) + 1:
        raise ValueError("Checkpoint page sequence is inconsistent")
    ids = {job_id for page in state.page_ids for job_id in page}
    if ids != set(state.jobs) or any(key != job.ats_id for key, job in state.jobs.items()):
        raise ValueError("Checkpoint job IDs are inconsistent")
    state.resumed = True
    return state
