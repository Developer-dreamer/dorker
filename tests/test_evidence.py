"""Offline evidence API and service contracts; no database/model connections."""

import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from litestar.testing import TestClient
from pydantic import ValidationError

from src.api.app import create_app
from src.api.evidence_models import EvidenceCreate, EvidencePatch
from src.api.services.evidence import EvidenceService, EvidenceServiceProtocol
from src.api.services.profile import ProfileServiceProtocol
from src.database.evidence import EvidenceConflict, EvidenceNotFound, EvidenceRepository
from src.database.postgres.evidence import EvidenceRepositoryPostgres
from src.shared.models.evidence import (
    Evidence,
    EvidenceFolder,
    EvidenceUpdate,
    MatchEvidence,
    MatchEvidenceUpdate,
    NewEvidence,
)
from src.shared.models.profile import UserProfile

NOW = datetime.now(timezone.utc)


def evidence():
    return Evidence(
        id=uuid4(),
        folder_path="/cv",
        title="Role",
        text="  Exact authored text\n",
        use_for_matching=False,
        created_at=NOW,
        updated_at=NOW,
    )


class EvidenceApiTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock(spec=EvidenceServiceProtocol)
        self.item = evidence()
        self.match_id = uuid4()
        self.service.create_evidence.return_value = self.item
        self.service.get_evidence.return_value = self.item
        self.service.update_evidence.return_value = self.item
        self.service.list_evidence.return_value = [self.item]
        self.service.create_folder.return_value = EvidenceFolder(path="/cv", created_at=NOW)
        self.service.list_folders.return_value = []
        self.selection = MatchEvidence(
            match_id=self.match_id, initialized_at=NOW, evidence=[self.item], checked_ids=[]
        )
        self.service.get_match_evidence.return_value = self.selection
        self.service.set_match_evidence.return_value = self.selection
        self.profile = Mock(spec=ProfileServiceProtocol)
        self.profile.get_profile.return_value = UserProfile(
            id=123, name=None, profile_links=[], created_at=NOW, updated_at=NOW
        )
        self.profile.update_profile.return_value = self.profile.get_profile.return_value
        self.client = TestClient(
            create_app(evidence_service=self.service, profile_service=self.profile),
            raise_server_exceptions=False,
        )

    def test_create_preserves_text_and_rejects_client_identity(self):
        body = {"folder_path": "/cv", "title": " Role ", "text": "  Exact authored text\n"}
        response = self.client.post("/api/evidence", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        request = self.service.create_evidence.call_args.args[0]
        self.assertEqual(request.title, "Role")
        self.assertEqual(request.text, body["text"])
        self.assertEqual(
            self.client.post("/api/evidence", json={**body, "user_id": 999}).status_code, 422
        )

    def test_invalid_paths_and_blank_inputs_are_rejected(self):
        for path in ["/", "cv", "/cv/", "/cv//job", "/cv/../job", "/cv\njob"]:
            with self.subTest(path=path):
                self.assertEqual(
                    self.client.post("/api/folders", json={"path": path}).status_code, 422
                )
        self.assertEqual(
            self.client.post(
                "/api/evidence", json={"folder_path": "/cv", "title": "X", "text": "  "}
            ).status_code,
            422,
        )
        self.service.create_folder.assert_not_called()

    def test_partial_patch_preserves_omitted_fields_and_no_move(self):
        response = self.client.patch(
            f"/api/evidence/{self.item.id}", json={"use_for_matching": True}
        )
        self.assertEqual(response.status_code, 200, response.text)
        patch = self.service.update_evidence.call_args.args[1]
        self.assertEqual(patch.model_fields_set, {"use_for_matching"})
        for body in [{}, {"text": None}, {"title": " "}, {"folder_path": "/elsewhere"}]:
            self.assertEqual(
                self.client.patch(f"/api/evidence/{self.item.id}", json=body).status_code, 422
            )

    def test_folder_and_evidence_deletion_and_conflicts(self):
        self.assertEqual(self.client.post("/api/folders", json={"path": "/cv"}).status_code, 201)
        self.service.delete_folder.side_effect = EvidenceConflict("Folder is not empty.")
        self.assertEqual(
            self.client.delete("/api/folders", params={"path": "/cv"}).status_code, 409
        )
        self.service.delete_folder.side_effect = None
        self.assertEqual(
            self.client.delete("/api/folders", params={"path": "/cv"}).status_code, 204
        )
        self.assertEqual(self.client.delete(f"/api/evidence/{self.item.id}").status_code, 204)

    def test_selection_empty_is_valid_duplicates_rejected(self):
        url = f"/api/matches/{self.match_id}/evidence"
        response = self.client.patch(url, json={"checked_ids": []})
        self.assertEqual(response.status_code, 200, response.text)
        self.service.set_match_evidence.assert_awaited_once_with(
            self.match_id, MatchEvidenceUpdate(checked_ids=[])
        )
        self.assertIsNotNone(response.json()["initialized_at"])
        self.assertEqual(
            self.client.patch(url, json={"checked_ids": [str(self.item.id)] * 2}).status_code, 422
        )
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_missing_and_malformed_ids(self):
        self.service.get_evidence.side_effect = EvidenceNotFound("Evidence not found.")
        self.assertEqual(self.client.get(f"/api/evidence/{uuid4()}").status_code, 404)
        self.assertEqual(self.client.get("/api/evidence/invalid").status_code, 422)
        self.assertEqual(self.client.get("/api/matches/invalid/evidence").status_code, 422)

    def test_list_filters_and_profile_patch(self):
        response = self.client.get(
            "/api/evidence", params={"folder_path": "/cv", "matching_only": "true"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.service.list_evidence.assert_awaited_once_with("/cv", True)
        self.assertEqual(self.client.get("/api/profile").status_code, 200)
        self.assertEqual(self.client.patch("/api/profile", json={"name": None}).status_code, 200)
        self.assertEqual(self.profile.update_profile.call_args.args[0].model_fields_set, {"name"})
        self.assertEqual(
            self.client.patch("/api/profile", json={"profile_links": ["not a url"]}).status_code,
            422,
        )

    def test_openapi_exposes_evidence_contract(self):
        schema = self.client.get("/openapi.json").json()
        for path in [
            "/api/profile",
            "/api/folders",
            "/api/evidence",
            "/api/evidence/{id}",
            "/api/matches/{id}/evidence",
        ]:
            self.assertIn(path, schema["paths"])
        self.assertIn("delete", schema["paths"]["/api/folders"])

    def test_unconfigured_service_and_delete_cors(self):
        client = TestClient(create_app(), raise_server_exceptions=False)
        self.assertEqual(client.get("/api/folders").status_code, 503)
        response = client.options(
            "/api/evidence",
            headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "DELETE"},
        )
        self.assertIn("DELETE", response.headers["access-control-allow-methods"])


class EvidenceServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.repo = Mock(spec=EvidenceRepository)
        self.service = EvidenceService(self.repo, user_id=123)
        self.item = evidence()

    async def test_uuid7_generated_server_side_and_owner_scoped(self):
        self.repo.create_evidence.return_value = self.item
        await self.service.create_evidence(
            EvidenceCreate(folder_path="/cv", title="Role", text="Text")
        )
        args = self.repo.create_evidence.call_args.args
        self.assertEqual(args[0], 123)
        self.assertIsInstance(args[1], NewEvidence)
        self.assertEqual(args[1].id.version, 7)
        self.assertEqual(
            (args[1].folder_path, args[1].title, args[1].text, args[1].use_for_matching),
            ("/cv", "Role", "Text", False),
        )

    async def test_context_loads_only_checked_current_text(self):
        other = evidence()
        self.repo.get_match_evidence.return_value = MatchEvidence(
            match_id=uuid4(),
            initialized_at=NOW,
            evidence=[self.item, other],
            checked_ids=[other.id],
        )
        materials = await self.service.selected_materials(uuid4())
        self.assertEqual([(m.id, m.text) for m in materials], [(str(other.id), other.text)])
        self.repo.get_match_evidence.assert_awaited_once()
        self.assertEqual(self.repo.get_match_evidence.call_args.args[0], 123)

    async def test_matching_and_partial_profile_updates(self):
        self.repo.list_evidence.return_value = [self.item]
        self.assertEqual((await self.service.matching_materials())[0].text, self.item.text)
        self.repo.list_evidence.assert_awaited_once_with(123, folder_path=None, matching_only=True)
        update = EvidenceUpdate(text="New text")
        await self.service.update_evidence(self.item.id, update)
        self.repo.update_evidence.assert_awaited_once_with(123, self.item.id, update)
        self.assertIs(self.repo.update_evidence.call_args.args[2], update)

    async def test_selection_command_passes_unchanged_to_repository(self):
        update = MatchEvidenceUpdate(checked_ids=[self.item.id])
        match_id = uuid4()
        await self.service.set_match_evidence(match_id, update)
        self.repo.set_match_evidence.assert_awaited_once_with(123, match_id, update)
        self.assertIs(self.repo.set_match_evidence.call_args.args[2], update)

    async def test_invalid_service_path_and_duplicate_ids_never_reach_repository(self):
        with self.assertRaises(ValueError):
            await self.service.create_folder("/bad/../path")
        with self.assertRaises(ValueError):
            await self.service.set_match_evidence(
                uuid4(), MatchEvidenceUpdate(checked_ids=[self.item.id] * 2)
            )
        self.repo.create_folder.assert_not_called()
        self.repo.set_match_evidence.assert_not_called()

    def test_null_and_empty_patch_validation(self):
        for data in [{}, {"text": None}, {"text": "\n"}]:
            with self.assertRaises(ValidationError):
                EvidencePatch(**data)


class EvidenceRepositoryDomainTests(unittest.IsolatedAsyncioTestCase):
    """Use driver-like rows to verify the repository's public domain boundary."""

    def setUp(self):
        self.item = evidence()
        self.row = {**self.item.model_dump(), "user_id": 123, "storage_only": "private"}
        self.conn = SimpleNamespace(
            fetchrow=AsyncMock(return_value=self.row),
            fetch=AsyncMock(return_value=[self.row]),
            fetchval=AsyncMock(return_value=123),
            execute=AsyncMock(),
        )

        @asynccontextmanager
        async def transaction(**options):
            yield

        @asynccontextmanager
        async def acquire():
            yield self.conn

        self.conn.transaction = transaction
        self.repo = EvidenceRepositoryPostgres(SimpleNamespace(acquire=acquire))

    def assert_domain_evidence(self, result):
        self.assertIs(type(result), Evidence)
        self.assertEqual(result, self.item)
        self.assertNotIn("user_id", result.model_dump())
        self.assertNotIn("storage_only", result.model_dump())
        self.assertNotIn("checked", result.model_dump())

    async def test_read_and_list_hide_storage_columns(self):
        self.assert_domain_evidence(await self.repo.get_evidence(123, self.item.id))
        items = await self.repo.list_evidence(123, folder_path="/cv")
        self.assert_domain_evidence(items[0])
        self.conn.fetchval.assert_awaited_once_with(
            "SELECT EXISTS(SELECT 1 FROM folders WHERE user_id=$1 AND path=$2)", 123, "/cv"
        )

    async def test_create_binds_typed_identity_and_returns_domain_entity(self):
        draft = NewEvidence(
            id=self.item.id, folder_path="/cv", title=self.item.title, text=self.item.text
        )
        self.assert_domain_evidence(await self.repo.create_evidence(123, draft))
        self.assertEqual(
            self.conn.fetchrow.call_args.args[1:],
            (draft.id, 123, draft.folder_path, draft.title, draft.text, False),
        )

    async def test_edit_serializes_only_supplied_domain_fields(self):
        self.assert_domain_evidence(
            await self.repo.update_evidence(
                123, self.item.id, EvidenceUpdate(use_for_matching=False)
            )
        )
        sql, *args = self.conn.fetchrow.call_args.args
        self.assertIn("use_for_matching=$3", sql)
        self.assertNotIn("title=", sql)
        self.assertNotIn("text=", sql)
        self.assertEqual(args, [123, self.item.id, False])

    async def test_selection_converts_storage_check_column_to_domain_selection(self):
        match_id = uuid4()
        self.conn.fetchrow.return_value = {"id": match_id, "evidence_initialized_at": NOW}
        self.conn.fetch.return_value = [{**self.row, "checked": True}]
        result = await self.repo.get_match_evidence(123, match_id)
        self.assertIs(type(result), MatchEvidence)
        self.assertEqual(result.checked_ids, [self.item.id])
        self.assertEqual(result.initialized_at, NOW)
        self.assert_domain_evidence(result.evidence[0])
