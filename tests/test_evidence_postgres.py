"""Opt-in integration test. Never reads PG_DSN or connects to dorker_db.

EVIDENCE_TEST_DSN must identify an isolated database named evidence_*_test,
with migration 012 installed. Only synthetic fixtures are created here.
"""

import asyncio
import os
import unittest
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg

from src.api.evidence_models import EvidenceCreate, EvidencePatch
from src.api.profile_models import ProfilePatch
from src.api.services.evidence import EvidenceService
from src.api.services.profile import ProfileService
from src.database.evidence import EvidenceConflict, EvidenceNotFound
from src.database.postgres.evidence import EvidenceRepositoryPostgres
from src.database.postgres.profile import ProfileRepositoryPostgres
from src.shared.models.evidence import MatchEvidenceUpdate

DSN = os.environ.get("EVIDENCE_TEST_DSN")


@unittest.skipUnless(DSN, "Set EVIDENCE_TEST_DSN to an isolated evidence_*_test database.")
class EvidencePostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database = urlparse(DSN).path.lstrip("/")
        if not (database.startswith("evidence_") and database.endswith("_test")):
            raise RuntimeError("Refusing a non-test database.")
        self.pool = await asyncpg.create_pool(DSN, min_size=1, max_size=4)
        self.repo = EvidenceRepositoryPostgres(self.pool)
        self.owner = 7001
        self.service = EvidenceService(self.repo, user_id=self.owner)
        self.other = EvidenceService(self.repo, user_id=7002)
        self.match_id = uuid4()
        async with self.pool.acquire() as conn:
            # Minimal isolated test schema only; never fixtures in a production database.
            await conn.execute("INSERT INTO jobs(id) VALUES('evidence-test-job')")
            await conn.execute(
                "INSERT INTO matches(id,job_id) VALUES($1,$2)", self.match_id, "evidence-test-job"
            )

    async def asyncTearDown(self):
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM matches WHERE id=$1", self.match_id)
            await conn.execute("DELETE FROM evidence WHERE user_id=ANY($1::bigint[])", [7001, 7002])
            await conn.execute("DELETE FROM folders WHERE user_id=ANY($1::bigint[])", [7001, 7002])
            await conn.execute("DELETE FROM users WHERE id=ANY($1::bigint[])", [7001, 7002])
            await conn.execute("DELETE FROM jobs WHERE id='evidence-test-job'")
        await self.pool.close()

    async def test_persistent_library_and_atomic_selection(self):
        profile_service = ProfileService(ProfileRepositoryPostgres(self.pool), user_id=self.owner)
        profile = await profile_service.get_profile()
        self.assertEqual(profile.id, self.owner)
        updated = await profile_service.update_profile(
            ProfilePatch(name="Synthetic", profile_links=["https://example.com/"])
        )
        self.assertEqual(updated.name, "Synthetic")
        self.assertEqual(updated.model_dump(mode="json")["profile_links"], ["https://example.com/"])
        await self.service.create_folder("/race")
        results = await asyncio.gather(
            self.service.create_folder("/race/child"),
            self.service.delete_folder("/race"),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(result, Exception) for result in results), 1)
        paths = [folder.path for folder in await self.service.list_folders()]
        if "/race/child" in paths:
            self.assertIn("/race", paths)
            await self.service.delete_folder("/race/child")
            await self.service.delete_folder("/race")
        await self.service.create_folder("/cv")
        await self.service.create_folder("/cv/experience")
        await self.service.create_folder("/cv%")
        # A percent sign is a literal path character, not a LIKE wildcard.
        await self.service.delete_folder("/cv%")
        with self.assertRaises(EvidenceConflict):
            await self.service.delete_folder("/cv")
        with self.assertRaises(EvidenceConflict):
            await self.service.create_folder("/cv")
        with self.assertRaises(EvidenceNotFound):
            await self.service.create_folder("/missing/child")
        await self.other.create_folder("/private")
        with self.assertRaises(EvidenceNotFound):
            await self.service.create_evidence(
                EvidenceCreate(folder_path="/private", title="X", text="X")
            )
        item = await self.service.create_evidence(
            EvidenceCreate(
                folder_path="/cv/experience",
                title="Role",
                text="Authored text",
                use_for_matching=True,
            )
        )
        self.assertEqual(item.id.version, 7)
        self.assertEqual(
            [m.text for m in await self.service.matching_materials()], ["Authored text"]
        )
        with self.assertRaises(EvidenceNotFound):
            await self.other.get_evidence(item.id)
        with self.assertRaises(EvidenceNotFound):
            await self.other.delete_evidence(item.id)
        with self.assertRaises(EvidenceConflict):
            await self.service.delete_folder("/cv/experience")
        selected = await self.service.set_match_evidence(
            self.match_id, MatchEvidenceUpdate(checked_ids=[item.id])
        )
        self.assertEqual(selected.checked_ids, [item.id])
        with self.assertRaises(EvidenceNotFound):
            await self.service.set_match_evidence(
                self.match_id, MatchEvidenceUpdate(checked_ids=[uuid4()])
            )
        self.assertEqual(
            (await self.service.get_match_evidence(self.match_id)).checked_ids, [item.id]
        )
        await self.service.update_evidence(item.id, EvidencePatch(text="Updated text"))
        self.assertEqual(
            (await self.service.selected_materials(self.match_id))[0].text, "Updated text"
        )
        empty = await self.service.set_match_evidence(
            self.match_id, MatchEvidenceUpdate(checked_ids=[])
        )
        self.assertIsNotNone(empty.initialized_at)
        self.assertEqual(empty.checked_ids, [])
        await self.service.set_match_evidence(
            self.match_id, MatchEvidenceUpdate(checked_ids=[item.id])
        )
        await self.service.delete_evidence(item.id)
        self.assertEqual((await self.service.get_match_evidence(self.match_id)).checked_ids, [])
        await self.service.delete_folder("/cv/experience")
        await self.service.delete_folder("/cv")
        with self.assertRaises(EvidenceNotFound):
            await self.service.get_match_evidence(uuid4())
