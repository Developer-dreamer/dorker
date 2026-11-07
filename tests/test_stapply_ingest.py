"""Offline ingestion regressions using synthetic jobs and a mocked database."""

import argparse
import csv
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from migrations.postgres.python import ingest_stapply_csv as ingest

HEADERS = ["ats_type", "ats_id", "url", "title", "description"]


def jobs_csv(ids: list[str], description: str = "Example description") -> bytes:
    text = io.StringIO(newline="")
    writer = csv.writer(text)
    writer.writerow(HEADERS)
    for job_id in ids:
        writer.writerow(
            ["example", job_id, f"https://example.invalid/jobs/{job_id}", "Engineer", description]
        )
    return text.getvalue().encode("utf-8")


class ChunkedStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], *, fail: bool = False) -> None:
        self.chunks = chunks
        self.fail = fail
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.fail:
            raise httpx.ReadError("Synthetic interrupted download")

    async def aclose(self) -> None:
        self.closed = True


class IngestTests(unittest.IsolatedAsyncioTestCase):
    async def run_main(
        self, stream: ChunkedStream, *, dry_run: bool = False,
        existing_ids: set[str] | None = None, **options: object
    ) -> tuple[int, AsyncMock, str]:
        args = argparse.Namespace(
            dsn="postgresql://example.invalid/synthetic", file=None, url=None,
            ats=None, all=True, batch_size=2, limit=None, dry_run=dry_run,
            skip_stapply_companies_meta=True,
        )
        for key, value in options.items():
            setattr(args, key, value)
        conn = AsyncMock()
        conn.fetch.return_value = []
        seen = set(existing_ids or ())

        async def execute(query: str, *params: object) -> str:
            if query != ingest.INSERT_JOBS_SQL:
                return "INSERT 0 1"  # Sentinel company.
            ids = params[0]
            inserted = sum(job_id not in seen for job_id in ids)
            seen.update(ids)
            return f"INSERT 0 {inserted}"

        conn.execute.side_effect = execute

        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("manifest.json"):
                return httpx.Response(
                    200, json={"all": {"csv": "https://example.invalid/all.csv"}}
                )
            return httpx.Response(200, stream=stream)

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with (
            patch.object(ingest.asyncpg, "connect", return_value=conn),
            patch.object(ingest.httpx, "AsyncClient", return_value=client),
            self.assertLogs(ingest.log, level="INFO") as logs,
        ):
            status = await ingest.async_main(args)
        conn.close.assert_awaited_once()
        return status, conn, "\n".join(logs.output)

    async def test_remote_csv_matches_local_across_chunk_boundaries(self) -> None:
        description = 'First line\nSecond "quoted" line\r\nUnicode: café\u2028next\x0bpart'
        data = b"\xef\xbb\xbf" + jobs_csv(["1", "2"], description).rstrip(b"\r\n")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.csv"
            path.write_bytes(data)
            expected = [row async for row in ingest.stream_csv_rows_from_file(path)]
        for size in (1, 7, len(data)):
            with self.subTest(chunk_size=size):
                stream = ChunkedStream([data[i:i + size] for i in range(0, len(data), size)])
                client = httpx.AsyncClient(
                    transport=httpx.MockTransport(
                        lambda request, stream=stream: httpx.Response(200, stream=stream)
                    )
                )
                async with client:
                    actual = [
                        row async for row in ingest.stream_csv_rows_from_url(
                            client, "https://example.invalid/jobs.csv"
                        )
                    ]
                self.assertEqual(actual, expected)
                self.assertEqual(actual[0]["description"], description)

    async def test_dry_run_never_writes_even_for_unknown_companies(self) -> None:
        status, conn, logs = await self.run_main(ChunkedStream([jobs_csv(["1", "2"])]), dry_run=True)
        self.assertEqual(status, 0)
        conn.execute.assert_not_awaited()
        self.assertIn("Candidates (existence not checked): 2", logs)
        self.assertIn("Existing skipped: not checked", logs)
        self.assertIn("2 unknown id=-1", logs)

    async def test_success_reports_inserts_separately_from_valid_rows(self) -> None:
        status, conn, logs = await self.run_main(ChunkedStream([jobs_csv(["1", "1", "2"])]))
        self.assertEqual(status, 0)
        self.assertIn("Read: 3 | Valid: 3 | Skipped: 0", logs)
        self.assertIn("Inserted: 2", logs)
        self.assertEqual(conn.execute.await_count, 2)  # Sentinel and one deduplicated batch.
        self.assertEqual(conn.execute.await_args.args[1], ["example:1", "example:2"])

    async def test_existing_ids_are_skipped_and_only_new_ids_are_counted(self) -> None:
        status, conn, logs = await self.run_main(
            ChunkedStream([jobs_csv(["1", "2"])]), existing_ids={"example:1"}
        )
        self.assertEqual(status, 0)
        self.assertIn("Inserted: 1 | Existing skipped: 1", logs)
        query = conn.execute.await_args.args[0]
        self.assertIn("ON CONFLICT (id) DO NOTHING", query)
        self.assertNotIn("DO UPDATE", query)

    async def test_all_existing_is_success_with_zero_inserted(self) -> None:
        status, _, logs = await self.run_main(
            ChunkedStream([jobs_csv(["1", "2"])]), existing_ids={"example:1", "example:2"}
        )
        self.assertEqual(status, 0)
        self.assertIn("Inserted: 0 | Existing skipped: 2 | Failed sources: 0", logs)

    async def test_failed_download_preserves_completed_batch_counts(self) -> None:
        status, conn, logs = await self.run_main(
            ChunkedStream([jobs_csv(["1", "2", "3"])], fail=True)
        )
        self.assertEqual(status, 1)
        self.assertIn("Synthetic interrupted download", logs)
        self.assertIn("SUMMARY (FAILED)", logs)
        self.assertIn("Read: 3 | Valid: 3", logs)
        self.assertIn("Inserted: 2", logs)
        self.assertIn("Failed sources: 1", logs)
        self.assertEqual(conn.execute.await_count, 2)

    async def test_failed_database_batch_is_not_counted_as_inserted(self) -> None:
        with patch.object(ingest, "flush_batch", side_effect=RuntimeError("Synthetic DB failure")):
            status, _, logs = await self.run_main(ChunkedStream([jobs_csv(["1", "2"])]))
        self.assertEqual(status, 1)
        self.assertIn("Synthetic DB failure", logs)
        self.assertIn("Valid: 2", logs)
        self.assertIn("Inserted: 0", logs)

    async def test_invalid_headers_fail_before_reading_the_rest_of_the_download(self) -> None:
        for ending in (b"\n", b""):
            stream = ChunkedStream([b"company,description" + ending], fail=bool(ending))
            status, conn, logs = await self.run_main(stream, dry_run=True)
            self.assertEqual(status, 1)
            self.assertIn("missing required columns", logs)
            self.assertNotIn("Synthetic interrupted download", logs)
            conn.execute.assert_not_awaited()

    async def test_local_invalid_headers_fail_as_well(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.csv"
            path.write_text("company,description\nExample,Text\n")
            with self.assertRaisesRegex(ValueError, "missing required columns"):
                _ = [row async for row in ingest.stream_csv_rows_from_file(path)]

    async def test_all_invalid_rows_fail_with_reasons(self) -> None:
        data = b"ats_type,ats_id,url,title\nexample,,https://example.invalid/1,Engineer\n"
        status, _, logs = await self.run_main(ChunkedStream([data]), dry_run=True)
        self.assertEqual(status, 1)
        self.assertIn("empty required fields: ats_id", logs)
        self.assertIn("All 1 rows were rejected", logs)
        self.assertIn("Read: 1 | Valid: 0 | Skipped: 1", logs)

    async def test_empty_source_is_explicit(self) -> None:
        status, _, logs = await self.run_main(ChunkedStream([jobs_csv([])]), dry_run=True)
        self.assertEqual(status, 0)
        self.assertIn("Source contains no job rows", logs)
        self.assertIn("Read: 0 | Valid: 0", logs)

    async def test_limit_closes_download_without_fetching_an_extra_row(self) -> None:
        stream = ChunkedStream([jobs_csv(["1"])], fail=True)
        status, conn, logs = await self.run_main(stream, dry_run=True, limit=1)
        self.assertEqual(status, 0)
        self.assertTrue(stream.closed)
        conn.execute.assert_not_awaited()
        self.assertIn("Read: 1 | Valid: 1", logs)

    async def test_unknown_source_does_not_succeed_with_zero_jobs(self) -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"by_ats": {}}))
        ) as client:
            with self.assertRaisesRegex(ValueError, "No jobs CSV sources"):
                await ingest.resolve_urls_from_manifest(client, ["missing"], False)

    def test_nonpositive_batch_size_and_limit_are_rejected(self) -> None:
        for flag in ("--batch-size", "--limit"):
            for value in ("0", "-1"):
                with (
                    self.subTest(flag=flag, value=value),
                    patch("sys.argv", ["ingest_stapply_csv", flag, value]),
                    patch("sys.stderr", new=io.StringIO()),
                    self.assertRaises(SystemExit) as raised,
                ):
                    ingest.parse_args()
                self.assertEqual(raised.exception.code, 2)
