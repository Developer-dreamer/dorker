import asyncio
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from src.analytics.engine import MatchingEngine
from src.analytics.summary_worker import SummaryWorker
from src.shared.models import JobAnalytics, MatchedJob, SuitabilityTier


def job(match):
    return SimpleNamespace(id=match.job_id, title="Backend", match=match)


class SummaryWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.match = MatchedJob(job_id="job")
        self.repository = SimpleNamespace(
            get_pending_summary_match_ids=AsyncMock(return_value=[self.match.id]),
            get_job_by_match_id=AsyncMock(return_value=job(self.match)),
            update_summary=AsyncMock(),
        )
        self.generator = SimpleNamespace(
            generate=AsyncMock(return_value=JobAnalytics(summary="Scope"))
        )
        self.available = asyncio.Event()
        self.worker = SummaryWorker(
            self.repository, self.generator, self.available, Mock(), poll_seconds=0.01
        )

    async def test_startup_recovers_saved_match_without_in_memory_notification(self):
        saved = asyncio.Event()
        self.repository.update_summary.side_effect = lambda *_: saved.set()
        task = asyncio.create_task(self.worker.run())
        try:
            await asyncio.wait_for(saved.wait(), 1)
            self.repository.update_summary.assert_awaited_once_with(
                self.match.id, JobAnalytics(summary="Scope")
            )
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_failure_stays_pending_does_not_stop_other_summaries_then_retries(self):
        other = MatchedJob(job_id="other")
        self.repository.get_pending_summary_match_ids.return_value = [self.match.id, other.id]
        self.repository.get_job_by_match_id.side_effect = [
            job(self.match),
            job(other),
            job(self.match),
        ]
        self.generator.generate.side_effect = [
            RuntimeError("API down"),
            JobAnalytics(summary="second"),
            JobAnalytics(summary="retried"),
        ]
        self.assertFalse(await self.worker._process_pending())
        self.repository.update_summary.assert_awaited_once_with(
            other.id, JobAnalytics(summary="second")
        )
        self.repository.get_pending_summary_match_ids.return_value = [self.match.id]
        await self.worker._process_pending()
        self.repository.update_summary.assert_awaited_with(
            self.match.id, JobAnalytics(summary="retried")
        )

    async def test_no_summary_for_rejected_or_already_summarized_match(self):
        for match in [
            MatchedJob(job_id="job", suitability_tier=SuitabilityTier.REJECTED),
            MatchedJob(job_id="job", job_summary=JobAnalytics(summary="Existing")),
        ]:
            self.repository.get_job_by_match_id.return_value = job(match)
            await self.worker._process_pending()
        self.generator.generate.assert_not_awaited()

    async def test_database_failure_is_retried_by_polling(self):
        saved = asyncio.Event()
        self.repository.get_pending_summary_match_ids.side_effect = [
            RuntimeError("DB down"),
            [self.match.id],
        ]
        self.repository.update_summary.side_effect = lambda *_: saved.set()
        task = asyncio.create_task(self.worker.run())
        try:
            await asyncio.wait_for(saved.wait(), 1)
            self.assertEqual(self.repository.get_pending_summary_match_ids.await_count, 2)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_notification_during_query_is_not_lost(self):
        async def pending(_):
            self.available.set()
            return []

        self.repository.get_pending_summary_match_ids.side_effect = pending
        await self.worker._process_pending()
        self.assertTrue(self.available.is_set())

    async def test_save_failure_is_left_pending(self):
        self.repository.update_summary.side_effect = RuntimeError("DB down")
        self.assertFalse(await self.worker._process_pending())
        self.worker.logger.exception.assert_called_once()

    async def test_jev_continues_while_summary_api_is_waiting(self):
        api_started, release_api = asyncio.Event(), asyncio.Event()
        first = MatchedJob(job_id="first")
        rejected = MatchedJob(job_id="rejected", suitability_tier=SuitabilityTier.REJECTED)
        last = MatchedJob(job_id="last")
        saved = []

        async def generate(_):
            api_started.set()
            await release_api.wait()
            return JobAnalytics(summary="summary")

        async def classify(entry, _):
            if entry.id == "last":
                await api_started.wait()
            return entry.match

        async def save(match):
            saved.append(match)

        self.repository.get_pending_summary_match_ids.return_value = [first.id]
        self.repository.get_job_by_match_id.return_value = job(first)
        self.generator.generate.side_effect = generate
        engine = MatchingEngine.__new__(MatchingEngine)
        engine.logger = logging.getLogger("test")
        engine.job_repo = SimpleNamespace(
            get_matched_jobs=AsyncMock(return_value=[job(first), job(rejected), job(last)])
        )
        engine.job_clf = SimpleNamespace(classify=classify)
        engine.profile = None
        engine.match_repo = SimpleNamespace(save_match=save)
        engine.summary_available = self.available
        task = asyncio.create_task(self.worker.run())
        try:
            await asyncio.wait_for(engine.classify_background(), 1)
            self.assertEqual(saved, [first, rejected, last])
            self.repository.update_summary.assert_not_awaited()
            self.assertTrue(self.available.is_set())
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_rejected_and_unsaved_matches_do_not_notify_worker(self):
        engine = MatchingEngine.__new__(MatchingEngine)
        engine.logger = Mock()
        engine.job_repo = SimpleNamespace(
            get_matched_jobs=AsyncMock(return_value=[job(self.match)])
        )
        engine.profile = None
        engine.summary_available = self.available
        engine.job_clf = SimpleNamespace(classify=AsyncMock(return_value=self.match))
        engine.match_repo = SimpleNamespace(
            save_match=AsyncMock(side_effect=RuntimeError("DB down"))
        )
        await engine.classify_background()
        self.assertFalse(self.available.is_set())
        self.match.suitability_tier = SuitabilityTier.REJECTED
        engine.match_repo.save_match.side_effect = None
        await engine.classify_background()
        self.assertFalse(self.available.is_set())
