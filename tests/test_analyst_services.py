import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from orchestration.analyst import classify_periodically, run_services


class AnalystServicesTests(unittest.IsolatedAsyncioTestCase):
    def worker(self):
        return SimpleNamespace(run=AsyncMock(side_effect=asyncio.Event().wait))

    async def test_runs_immediately_then_waits_an_hour_between_cycles(self):
        engine = SimpleNamespace(classify_background=AsyncMock())
        calls = []

        async def interval(seconds):
            calls.append((seconds, engine.classify_background.await_count))
            if len(calls) == 2:
                raise asyncio.CancelledError

        with patch("orchestration.analyst.asyncio.sleep", side_effect=interval):
            with self.assertRaises(asyncio.CancelledError):
                await classify_periodically(engine)
        self.assertEqual(calls, [(3600, 1), (3600, 2)])

    async def test_cycle_failure_does_not_prevent_later_cycles(self):
        engine = SimpleNamespace(
            classify_background=AsyncMock(side_effect=[RuntimeError("DB down"), None])
        )
        intervals = 0

        async def interval(_):
            nonlocal intervals
            intervals += 1
            if intervals == 2:
                raise asyncio.CancelledError

        with (
            patch("orchestration.analyst.asyncio.sleep", side_effect=interval),
            patch("orchestration.analyst.logger") as logger,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await classify_periodically(engine)
        self.assertEqual(engine.classify_background.await_count, 2)
        logger.exception.assert_called_once()

    async def test_telegram_runs_while_classification_is_busy_and_stop_cancels_it(self):
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def classify():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def telegram():
            await started.wait()
            self.assertFalse(cancelled.is_set())

        engine = SimpleNamespace(classify_background=classify)
        bot = SimpleNamespace(run=telegram)
        await run_services(engine, bot, self.worker())
        self.assertTrue(cancelled.is_set())

    async def test_bot_failure_cancels_classification_and_propagates(self):
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def classify():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def telegram():
            await started.wait()
            raise RuntimeError("Telegram failed")

        with self.assertRaisesRegex(RuntimeError, "Telegram failed"):
            await run_services(
                SimpleNamespace(classify_background=classify),
                SimpleNamespace(run=telegram),
                self.worker(),
            )
        self.assertTrue(cancelled.is_set())

    async def test_shutdown_during_hourly_wait_does_not_start_another_cycle(self):
        waiting = asyncio.Event()
        engine = SimpleNamespace(classify_background=AsyncMock())

        async def interval(_):
            waiting.set()
            await asyncio.Event().wait()

        async def telegram():
            await waiting.wait()

        with patch("orchestration.analyst.asyncio.sleep", side_effect=interval):
            await run_services(engine, SimpleNamespace(run=telegram), self.worker())
        engine.classify_background.assert_awaited_once()

    async def test_shutdown_cancels_summary_api_call_too(self):
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def summaries():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def telegram():
            await started.wait()

        await run_services(
            SimpleNamespace(classify_background=AsyncMock()),
            SimpleNamespace(run=telegram),
            SimpleNamespace(run=summaries),
        )
        self.assertTrue(cancelled.is_set())
