"""Synthetic pages matching the public listing DOM; no candidate data or network."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from src.scraping.djinni_snapshot import (
    Snapshot,
    SnapshotError,
    collect,
    load_snapshot,
    parse_listing,
)
from src.scraping.exceptions import ScraperError
from src.scraping.scrapers.djinni import DjinniScraper


def listing(page: int, ids: list[int], total: int = 3, last: int = 2) -> str:
    cards = "".join(
        f'<div class="job-item" id="job-item-{i}"><a class="job_item__header-link" '
        f'href="/jobs/{i}-engineer/"><header><h2 class="job-item__position">Engineer</h2>'
        '<span class="small">Example Co</span></header></a>'
        '<span class="location-text">Worldwide</span>'
        '<span class="js-truncated-text">Short preview</span>'
        '<span class="js-original-text"><p>Full description</p><ul><li>Python</li></ul></span>'
        "</div>"
        for i in ids
    )
    links = "".join(f'<a href="?page={p}">{p}</a>' for p in range(1, last + 1))
    return (
        f'<h1>Jobs at Djinni</h1><span>{total}</span><main id="jobs_main">{cards}</main>'
        f'<ul class="pagination"><li aria-current="page">{page}</li>{links}</ul>'
    )


class SnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "snapshot.json"
        self.requests: list[str] = []
        self.responses: list[httpx.Response] = []
        self.robots = "User-agent: *\nDisallow: /developers\n"
        self.sleep = patch("src.scraping.djinni_snapshot.asyncio.sleep", new_callable=AsyncMock)
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    async def run_collect(self, state: Snapshot | None = None, **kwargs: object) -> Snapshot:
        def respond(request: httpx.Request) -> httpx.Response:
            self.requests.append(str(request.url))
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text=self.robots)
            return self.responses.pop(0)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await collect(client, state or Snapshot(), self.path, **kwargs)

    async def test_complete_and_full_description(self) -> None:
        self.responses = [
            httpx.Response(200, text=listing(1, [1, 2])),
            httpx.Response(200, text=listing(2, [3])),
        ]
        state = await self.run_collect()
        self.assertEqual(state.status, "complete")
        self.assertEqual(len(state.jobs), 3)
        self.assertIn("<li>Python</li>", state.jobs["1"].description)
        self.assertEqual(len(self.requests), 3)  # robots + two listings, no detail fetches
        self.assertEqual(load_snapshot(self.path).status, "complete")

    async def test_200_challenge_preserves_progress_and_resume(self) -> None:
        for challenge in (
            httpx.Response(200, text="anything", headers={"cf-mitigated": "challenge"}),
            httpx.Response(200, text='<title>Just a moment...</title><form id="challenge-form">'),
            httpx.Response(429, headers={"Retry-After": "3600"}),
            httpx.Response(403, text="Denied"),
        ):
            with self.subTest(status=challenge.status_code, body=challenge.text):
                self.responses = [httpx.Response(200, text=listing(1, [1, 2])), challenge]
                state = await self.run_collect()
                self.assertEqual(state.status, "blocked")
                self.assertEqual(state.next_page, 2)
                self.assertEqual(len(state.jobs), 2)
                self.assertFalse(self.responses)
                self.responses = [httpx.Response(200, text=listing(2, [3]))]
                resumed = await self.run_collect(load_snapshot(self.path))
                self.assertEqual(resumed.status, "complete")
                self.assertTrue(resumed.resumed)

    async def test_invalid_second_page_is_never_empty_success(self) -> None:
        for body in (
            "<html>Unknown CAPTCHA</html>",
            listing(1, [1, 2]),
            listing(2, [1, 2]),
            listing(2, []),
        ):
            self.responses = [
                httpx.Response(200, text=listing(1, [1, 2])),
                httpx.Response(200, text=body),
            ]
            state = await self.run_collect()
            self.assertEqual(state.status, "incomplete")
            self.assertEqual(state.next_page, 2)
            self.assertEqual(len(state.jobs), 2)

    async def test_count_drift_or_missing_jobs_is_incomplete(self) -> None:
        for final in (listing(2, [2]), listing(2, [3], total=4)):
            self.responses = [
                httpx.Response(200, text=listing(1, [1, 2])),
                httpx.Response(200, text=final),
            ]
            self.assertEqual((await self.run_collect()).status, "incomplete")

    async def test_page_limit_is_paused_not_complete(self) -> None:
        self.responses = [httpx.Response(200, text=listing(1, [1, 2]))]
        state = await self.run_collect(max_pages=1)
        self.assertEqual(state.status, "paused")
        self.assertEqual(load_snapshot(self.path).next_page, 2)

    async def test_robots_disallow_prevents_listing_request(self) -> None:
        self.robots = "User-agent: *\nDisallow: /jobs/\n"
        state = await self.run_collect()
        self.assertEqual(state.status, "blocked")
        self.assertEqual(len(self.requests), 1)

    async def test_interruption_saves_previous_page(self) -> None:
        calls = 0

        async def delay(_: float) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise asyncio.CancelledError

        self.responses = [httpx.Response(200, text=listing(1, [1, 2]))]
        with patch("src.scraping.djinni_snapshot.asyncio.sleep", side_effect=delay):
            with self.assertRaises(asyncio.CancelledError):
                await self.run_collect()
        saved = load_snapshot(self.path)
        self.assertEqual(saved.status, "paused")
        self.assertEqual(saved.next_page, 2)

    def test_missing_card_description_and_external_pagination_fail(self) -> None:
        for body in (
            listing(1, [1, 2]).replace("js-original-text", "missing"),
            listing(1, [1, 2]).replace("?page=2", "https://example.org/?page=2"),
        ):
            with self.assertRaises(SnapshotError):
                parse_listing(body, 1)

    async def test_adapter_fails_closed(self) -> None:
        async def blocked(client: object, state: Snapshot, output: object) -> Snapshot:
            state.status, state.reason = "blocked", "CAPTCHA"
            return state

        with patch("src.scraping.scrapers.djinni.collect", side_effect=blocked):
            with self.assertRaisesRegex(ScraperError, "CAPTCHA"):
                await DjinniScraper("djinni").afetch()


if __name__ == "__main__":
    unittest.main()
