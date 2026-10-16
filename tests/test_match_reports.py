import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from src.analytics.engine import MatchingEngine
from src.database.postgres.match import MatchRepositoryPostgres


class MatchReportTests(unittest.IsolatedAsyncioTestCase):
    def repository(self, result="UPDATE 1"):
        connection = SimpleNamespace(execute=AsyncMock(return_value=result))

        @asynccontextmanager
        async def acquire():
            yield connection

        return MatchRepositoryPostgres(SimpleNamespace(acquire=acquire), None), connection

    async def test_atomic_update_sets_flag_message_and_timestamp_only(self):
        repository, connection = self.repository()
        match_id = uuid4()
        await repository.report_match(match_id, "  Wrong strategic assessment  ")
        query, message, saved_id = connection.execute.call_args.args
        self.assertEqual((message, saved_id), ("Wrong strategic assessment", match_id))
        self.assertIn("reported = TRUE", query)
        self.assertIn("report_message = $1", query)
        self.assertIn("updated_at = CURRENT_TIMESTAMP", query)
        self.assertNotIn("suitability_tier", query)
        self.assertNotIn("pipeline_status", query)

    async def test_empty_and_missing_match_reports_fail(self):
        repository, connection = self.repository("UPDATE 0")
        with self.assertRaises(ValueError):
            await repository.report_match(uuid4(), "  ")
        connection.execute.assert_not_awaited()
        with self.assertRaisesRegex(ValueError, "no longer exists"):
            await repository.report_match(uuid4(), "Wrong tier")

    async def test_engine_forwards_report_to_repository(self):
        engine = MatchingEngine.__new__(MatchingEngine)
        engine.match_repo = SimpleNamespace(report_match=AsyncMock())
        match_id = uuid4()
        await engine.report_match(match_id, "Wrong tier")
        engine.match_repo.report_match.assert_awaited_once_with(match_id, "Wrong tier")
