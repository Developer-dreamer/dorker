"""Offline checks for the unwired UUID-reference repository."""

import copy
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from src.database.postgres.application_packet import packet_from_record
from src.database.postgres.application_packet_materials import (
    ApplicationPacketMaterialsRepositoryPostgres,
)
from src.shared.models import (
    ApplicationGeneratedResponse,
    ApplicationPacket,
    Material,
    RuntimeVersion,
)


@asynccontextmanager
async def transaction(**kwargs):
    yield


class MaterialsRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.evidence_id = uuid4()
        self.match_id = uuid4()
        self.raw = dict(
            id=7,
            job_id="synthetic",
            match_id=self.match_id,
            user_id=11,
            materials=[self.evidence_id],
            materials_snapshot=[{"id": "old", "text": "Historical snapshot"}],
            questions=[],
            debug=None,
            cover_letter="Draft",
            created_at=datetime.now(timezone.utc),
        )
        self.conn = SimpleNamespace(
            fetch=AsyncMock(return_value=[{"id": self.evidence_id, "text": "Current evidence"}]),
            fetchrow=AsyncMock(return_value=self.raw),
            fetchval=AsyncMock(return_value=1),
            transaction=transaction,
        )

        @asynccontextmanager
        async def acquire():
            yield self.conn

        self.repo = ApplicationPacketMaterialsRepositoryPostgres(
            SimpleNamespace(acquire=acquire),
            RuntimeVersion(model="synthetic", version="0.3.1", iteration=1, min_version="0.3.0"),
            user_id=11,
        )

    async def test_reference_read_uses_current_text_without_changing_snapshot(self):
        original = copy.deepcopy(self.raw)
        packet = await self.repo.get_packet_by_id(7)
        self.assertEqual(
            packet.materials, [Material(id=str(self.evidence_id), text="Current evidence")]
        )
        self.assertEqual(self.raw, original)
        self.assertEqual(
            packet_from_record(self.raw).materials, [Material(id="old", text="Historical snapshot")]
        )
        self.assertEqual(self.conn.fetchrow.call_args.args[1:], (7, 11))

    async def test_missing_or_foreign_evidence_fails_instead_of_falling_back(self):
        self.conn.fetch.return_value = []
        with self.assertRaisesRegex(ValueError, "missing or inaccessible"):
            await self.repo.get_packet_by_id(7)
        self.raw["user_id"] = 12
        with self.assertRaises(LookupError):
            await self.repo.get_packet_by_id(7)

    async def test_empty_references_are_empty_even_with_nonempty_snapshot(self):
        self.raw["materials"] = []
        packet = await self.repo.get_packet_by_id(7)
        self.assertEqual(packet.materials, [])
        self.conn.fetch.assert_not_awaited()

    async def test_saving_writes_owned_uuid_references_only(self):
        packet = ApplicationPacket(
            job_id="synthetic",
            match_id=self.match_id,
            materials=[Material(id=str(self.evidence_id), text="Input text")],
        )
        saved = await self.repo.save_packet(packet)
        query, *args = self.conn.fetchrow.call_args.args
        self.assertIn("materials,user_id", query)
        self.assertNotIn("materials_snapshot", query)
        self.assertEqual(args[2], [self.evidence_id])
        self.assertEqual(args[3], 11)
        self.assertEqual(saved.materials[0].text, "Current evidence")
        self.assertEqual(packet.id, 7)

    async def test_invalid_references_never_insert(self):
        packet = ApplicationPacket(
            job_id="synthetic",
            match_id=self.match_id,
            materials=[Material(id="profile.old.path", text="Text")],
        )
        with self.assertRaises(ValueError):
            await self.repo.save_packet(packet)
        packet.materials = [Material(id=str(self.evidence_id), text="Text")] * 2
        with self.assertRaises(ValueError):
            await self.repo.save_packet(packet)
        self.conn.fetchrow.assert_not_awaited()

    async def test_list_resolves_a_whole_page_in_one_evidence_query(self):
        self.conn.fetchval.side_effect = [True, 2]
        self.conn.fetch.side_effect = [
            [self.raw, self.raw],
            [{"id": self.evidence_id, "text": "Current evidence"}],
        ]
        packets, total = await self.repo.list_packets(self.match_id, 50)
        self.assertEqual(total, 2)
        self.assertEqual(len(packets), 2)
        self.assertEqual(self.conn.fetch.await_count, 2)
        self.assertEqual(packets[0].materials, packets[1].materials)

    async def test_partial_update_is_owner_scoped_and_rehydrates(self):
        result = await self.repo.update_packet(7, ApplicationGeneratedResponse(cover_letter=None))
        query, *args = self.conn.fetchrow.call_args.args
        self.assertIn("a.user_id=$2", query)
        self.assertNotIn("questions=", query)
        self.assertEqual(args, [7, 11, None])
        self.assertEqual(result.materials[0].text, "Current evidence")
