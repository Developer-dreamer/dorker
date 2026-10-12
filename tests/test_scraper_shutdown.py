import ast
import asyncio
import logging
import signal
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from src.scraping.pipeline_engine.engine import RunEngine
from src.scraping.pipeline_engine.scraper_runner import ScraperRunner


def entry_function(name, **namespace):
    tree = ast.parse(Path("orchestration/scraper.py").read_text())
    node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
    ns = dict(
        asyncio=asyncio,
        signal=signal,
        Any=object,
        Dict=dict,
        List=list,
        Dashboard=object,
        Live=object,
        logger=logging.getLogger("shutdown-test"),
    )
    ns.update(namespace)
    exec(compile(ast.Module(body=[node], type_ignores=[]), "<entrypoint>", "exec"), ns)
    return ns[name]


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_fetch_is_cancelled_without_sync_wrapper(self):
        started, stopped = asyncio.Event(), asyncio.Event()

        class Scraper:
            def __init__(self, *args, **kwargs):
                pass

            def fetch(self):
                raise AssertionError("sync wrapper must not run")

            async def afetch(self):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()

        runner = object.__new__(ScraperRunner)
        task = asyncio.create_task(runner._run_scraper(Scraper, "synthetic"))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(stopped.is_set())

    async def test_ui_cancellation_does_not_acknowledge_missing_item(self):
        queue = asyncio.Queue()
        task = asyncio.create_task(entry_function("ui_worker")(queue, Mock(), Mock()))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        queue.put_nowait(None)
        await entry_function("ui_worker")(queue, Mock(), Mock())
        await asyncio.wait_for(queue.join(), 1)

    async def test_engine_drains_queued_jobs_on_cancellation(self):
        engine = object.__new__(RunEngine)
        engine.logger = logging.getLogger("shutdown-test")
        engine.db_writer_queue = asyncio.Queue()
        engine.job_repository = SimpleNamespace(
            save_job_batch=AsyncMock(), evict_stale_deleted_descriptions=AsyncMock()
        )
        started = asyncio.Event()

        async def worker(ats):
            await engine.db_writer_queue.put("synthetic-job")
            started.set()
            await asyncio.Event().wait()

        engine._run_single_ats = worker
        task = asyncio.create_task(engine.run([object()]))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(engine.db_writer_queue.join(), 1)
        engine.job_repository.save_job_batch.assert_awaited()
        engine.job_repository.evict_stale_deleted_descriptions.assert_not_awaited()

    async def test_repeated_signals_do_not_interrupt_cleanup(self):
        started, cleanup, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def main():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup.set()
                await finish.wait()

        handlers = {}
        loop = asyncio.get_running_loop()
        with patch.object(
            loop, "add_signal_handler", side_effect=lambda sig, cb: handlers.update({sig: cb})
        ):
            task = asyncio.create_task(entry_function("run_scraper", main_loop=main)())
            await started.wait()
            handlers[signal.SIGINT]()
            await cleanup.wait()
            handlers[signal.SIGINT]()
            handlers[signal.SIGTERM]()
            finish.set()
            await asyncio.wait_for(task, 1)


if __name__ == "__main__":
    unittest.main()
