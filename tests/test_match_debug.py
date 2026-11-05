import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from src.analytics.engine import MatchingEngine
from src.database.postgres.match import MatchRepositoryPostgres


class MatchDebugTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_stored_column_and_distinguishes_null_from_missing(self):
        connection = SimpleNamespace(fetchrow=AsyncMock(return_value={"debug": '{"raw": 1}'}))

        @asynccontextmanager
        async def acquire():
            yield connection

        repository = MatchRepositoryPostgres(SimpleNamespace(acquire=acquire), None)
        match_id = uuid4()
        self.assertEqual(await repository.get_match_debug(match_id), '{"raw": 1}')
        query, saved_id = connection.fetchrow.call_args.args
        self.assertEqual(saved_id, match_id)
        self.assertIn("SELECT m.debug", query)
        self.assertIn("j.deleted_at IS NULL", query)
        connection.fetchrow.return_value = {"debug": None}
        self.assertIsNone(await repository.get_match_debug(match_id))
        connection.fetchrow.return_value = None
        with self.assertRaisesRegex(LookupError, "Match not found"):
            await repository.get_match_debug(match_id)

    async def test_engine_returns_repository_value(self):
        engine = MatchingEngine.__new__(MatchingEngine)
        engine.match_repo = SimpleNamespace(get_match_debug=AsyncMock(return_value="raw output"))
        match_id = uuid4()
        self.assertEqual(await engine.get_match_debug(match_id), "raw output")
        engine.match_repo.get_match_debug.assert_awaited_once_with(match_id)
