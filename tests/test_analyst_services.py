import asyncio
import select
import signal
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from orchestration.analyst import AnalystServer, classify_periodically, run, run_services


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

    async def test_http_server_runs_while_classification_is_busy_and_stop_cancels_it(self):
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def classify():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def http_server():
            await started.wait()
            self.assertFalse(cancelled.is_set())

        engine = SimpleNamespace(classify_background=classify)
        server = SimpleNamespace(serve=http_server)
        await run_services(engine, server, self.worker())
        self.assertTrue(cancelled.is_set())

    async def test_server_failure_cancels_classification_and_propagates(self):
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def classify():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def http_server():
            await started.wait()
            raise RuntimeError("HTTP server failed")

        with self.assertRaisesRegex(RuntimeError, "HTTP server failed"):
            await run_services(
                SimpleNamespace(classify_background=classify),
                SimpleNamespace(serve=http_server),
                self.worker(),
            )
        self.assertTrue(cancelled.is_set())

    async def test_shutdown_during_hourly_wait_does_not_start_another_cycle(self):
        waiting = asyncio.Event()
        engine = SimpleNamespace(classify_background=AsyncMock())

        async def interval(_):
            waiting.set()
            await asyncio.Event().wait()

        async def http_server():
            await waiting.wait()

        with patch("orchestration.analyst.asyncio.sleep", side_effect=interval):
            await run_services(engine, SimpleNamespace(serve=http_server), self.worker())
        engine.classify_background.assert_awaited_once()

    async def test_shutdown_cancels_summary_api_call_too(self):
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def summaries():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def http_server():
            await started.wait()

        await run_services(
            SimpleNamespace(classify_background=AsyncMock()),
            SimpleNamespace(serve=http_server),
            SimpleNamespace(run=summaries),
        )
        self.assertTrue(cancelled.is_set())

    async def test_cancellation_drains_http_and_awaits_workers_before_returning(self):
        serving, draining, finish_request = asyncio.Event(), asyncio.Event(), asyncio.Event()
        classification_stopped, summaries_stopped = asyncio.Event(), asyncio.Event()

        async def classify():
            try:
                await asyncio.Event().wait()
            finally:
                classification_stopped.set()

        async def summaries():
            try:
                await asyncio.Event().wait()
            finally:
                summaries_stopped.set()

        async def http_server():
            serving.set()
            while not server.should_exit:
                await asyncio.sleep(0)
            draining.set()
            await finish_request.wait()

        server = SimpleNamespace(serve=http_server, should_exit=False)
        task = asyncio.create_task(
            run_services(
                SimpleNamespace(classify_background=classify),
                server,
                SimpleNamespace(run=summaries),
            )
        )
        try:
            await asyncio.wait_for(serving.wait(), timeout=1)
            task.cancel()
            await asyncio.wait_for(draining.wait(), timeout=1)
            await asyncio.wait_for(classification_stopped.wait(), timeout=1)
            await asyncio.wait_for(summaries_stopped.wait(), timeout=1)
            self.assertFalse(task.done())
        finally:
            finish_request.set()
            with self.assertRaises(asyncio.CancelledError):
                await task


class AnalystShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_signals_during_startup_wait_for_cleanup_and_restore_handlers(self):
        for sig in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=sig):
                original_handlers = {
                    sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)
                }
                cleaned_up = asyncio.Event()

                async def startup(sig=sig, cleaned_up=cleaned_up):
                    try:
                        signal.raise_signal(sig)
                        await asyncio.sleep(0)
                        self.fail("Startup continued after shutdown was requested.")
                    finally:
                        # A second signal must not interrupt awaited resource cleanup.
                        signal.raise_signal(sig)
                        await asyncio.sleep(0)
                        cleaned_up.set()

                with patch("orchestration.analyst._run", side_effect=startup):
                    await run()
                self.assertTrue(cleaned_up.is_set())
                for handled_signal, handler in original_handlers.items():
                    self.assertEqual(signal.getsignal(handled_signal), handler)

    async def test_external_cancellation_is_not_swallowed(self):
        started, cleaned_up = asyncio.Event(), asyncio.Event()

        async def startup():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned_up.set()

        with patch("orchestration.analyst._run", side_effect=startup):
            task = asyncio.create_task(run())
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(cleaned_up.is_set())

    async def test_uvicorn_keeps_the_analyst_signal_handlers(self):
        import uvicorn

        server = AnalystServer(uvicorn.Config("unused:app"))

        async def serve(sockets=None):
            signal.raise_signal(signal.SIGTERM)
            await asyncio.sleep(0)

        async def services():
            server._serve = serve
            await run_services(
                SimpleNamespace(classify_background=AsyncMock()), server, self.worker()
            )

        with patch("orchestration.analyst._run", side_effect=services):
            await run()
        self.assertTrue(server.should_exit)

    def worker(self):
        return SimpleNamespace(run=AsyncMock(side_effect=asyncio.Event().wait))


class AnalystProcessShutdownTests(unittest.TestCase):
    def test_signals_wake_an_idle_event_loop_and_exit_after_cleanup(self):
        child = """
import asyncio
from orchestration import analyst

async def isolated_runtime():
    print("ready", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await asyncio.sleep(0.01)
        print("resources closed", flush=True)

analyst._run = isolated_runtime
asyncio.run(analyst.run())
"""
        for sig in (signal.SIGINT, signal.SIGTERM):
            with (
                self.subTest(signal=sig),
                subprocess.Popen(
                    [sys.executable, "-c", child],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                ) as process,
            ):
                try:
                    self.assertTrue(select.select([process.stdout], [], [], 30)[0])
                    self.assertEqual(process.stdout.readline().strip(), "ready")
                    process.send_signal(sig)
                    stdout, stderr = process.communicate(timeout=10)
                    self.assertEqual(process.returncode, 0, stderr)
                    self.assertIn("resources closed", stdout)
                    self.assertNotIn("Traceback", stderr)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
