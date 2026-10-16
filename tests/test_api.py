"""HTTP adapter tests: no database, provider, or Telegram calls."""

import unittest
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from fastapi.testclient import TestClient

from src.analytics.discussion import DiscussionService
from src.analytics.protocols import MatchingProvider
from src.api import create_app
from src.shared.models import (
    ApplicationGeneratedResponse,
    ApplicationPacket,
    JobForAnalytics,
    Material,
)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.match_id = uuid4()
        self.job = JobForAnalytics(
            id="synthetic:job",
            title="Engineer",
            location="Remote",
            description="Synthetic job",
            salary_min=None,
            salary_max=None,
            salary_currency=None,
        )
        self.engine = Mock(spec=MatchingProvider)
        self.engine.get_job_by_match_id = AsyncMock(return_value=self.job)
        self.engine.get_matching_count = AsyncMock(return_value={"SUITABLE": 2})
        self.engine.get_matched_job = AsyncMock(return_value=None)
        self.engine.report_match = AsyncMock()
        self.engine.append_question = AsyncMock()
        self.engine.update_pipeline_status = AsyncMock()
        self.engine.generate_application_for_match = AsyncMock(
            return_value=ApplicationGeneratedResponse(cover_letter="Synthetic letter")
        )
        self.discussion = Mock(spec=DiscussionService)
        self.discussion.repository = Mock(history=AsyncMock(return_value=[]))
        self.discussion.profile = Mock()
        self.material = Material(id="experience.example", text="Synthetic experience")
        self.discussion.profile.get_materials.return_value = [self.material]
        self.discussion.profile.get_material.return_value = self.material
        self.packet = ApplicationPacket(job_id=self.job.id, match_id=self.match_id)
        self.discussion.confirmed_materials = AsyncMock(return_value=[self.material])
        self.discussion.selected_packet = AsyncMock(return_value=self.packet)
        self.discussion.record_action = AsyncMock()
        self.discussion.start = AsyncMock()
        self.discussion.finish = AsyncMock()
        self.discussion.send = AsyncMock(return_value="Synthetic answer")
        self.client = TestClient(create_app(self.engine, self.discussion, user_id=123, chat_id=123))
        self.url = f"/api/matches/{self.match_id}"

    def test_stream_auto_starts_and_returns_deltas_and_errors(self):
        async def stream(*args):
            yield {"type": "accepted", "message_id": "user-id"}
            yield {"type": "delta", "text": "Hello"}
            yield {"type": "done", "message_id": "answer-id", "text": "Hello"}

        self.discussion.stream_send = stream
        response = self.client.post(self.url + "/discussion/stream", json={"text": "Hi"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/x-ndjson", response.headers["content-type"])
        import json

        events = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual(
            [event["type"] for event in events],
            ["thinking", "evidence", "accepted", "delta", "done"],
        )
        self.discussion.start.assert_awaited_once_with(self.job, 123, 123)

        async def failed(*args):
            yield {"type": "accepted", "message_id": "user-id"}
            raise ValueError("Reply incomplete")

        self.discussion.stream_send = failed
        response = self.client.post(self.url + "/discussion/stream", json={"text": "Hi"})
        error = json.loads(response.text.splitlines()[-1])
        self.assertEqual(error, {"type": "error", "detail": "Reply incomplete", "saved": True})

    def test_debug_is_loaded_separately_by_match_id(self):
        self.engine.get_match_debug = AsyncMock(return_value='{"scores": {"technical": 3}}')
        response = self.client.get(self.url + "/debug")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"debug": '{"scores": {"technical": 3}}'})
        self.engine.get_match_debug.assert_awaited_once_with(self.match_id)
        self.engine.get_job_by_match_id.assert_not_awaited()
        self.engine.get_match_debug.return_value = None
        self.assertEqual(self.client.get(self.url + "/debug").json(), {"debug": None})
        self.engine.get_match_debug.side_effect = ValueError("Match not found.")
        self.assertEqual(self.client.get(self.url + "/debug").status_code, 404)

    def test_navigation_and_missing_match(self):
        self.assertEqual(self.client.get("/api/matches/counts").json(), {"SUITABLE": 2})
        self.assertIsNone(self.client.get("/api/matches/next?offset=3&tiers=STRETCH").json())
        self.assertEqual(self.client.get(self.url).json()["id"], self.job.id)
        self.engine.get_job_by_match_id.return_value = None
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url + "/generation", json={}).status_code, 404)
        self.engine.generate_application_for_match.assert_not_awaited()

    def test_validation_prevents_mutations(self):
        for suffix, body in [
            ("/reports", {"text": "  "}),
            ("/questions", {"text": "x" * 16001}),
            ("/generation", {"cover_letter": False, "questions": False}),
            ("/discussion/messages", {"text": "hi", "user_id": 999}),
        ]:
            self.assertEqual(self.client.post(self.url + suffix, json=body).status_code, 422)
        self.assertEqual(
            self.client.patch(self.url + "/status", json={"status": "BOGUS"}).status_code, 422
        )
        self.assertEqual(self.client.get("/api/matches/next?offset=-1").status_code, 422)
        self.assertEqual(self.client.get("/api/matches/not-a-uuid").status_code, 422)
        self.engine.report_match.assert_not_awaited()

    def test_generation_reuses_discussion_selection(self):
        response = self.client.post(self.url + "/generation", json={"questions": False})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["cover_letter"], "Synthetic letter")
        args, kwargs = self.engine.generate_application_for_match.call_args
        self.assertFalse(args[0].questions)
        self.assertEqual(kwargs, {"materials": [self.material], "selected_packet": self.packet})
        self.discussion.confirmed_materials.assert_awaited_once_with(self.match_id, 123, 123)

    def test_report_question_and_status(self):
        self.assertEqual(
            self.client.post(self.url + "/reports", json={"text": " report "}).status_code, 204
        )
        self.engine.report_match.assert_awaited_once_with(self.match_id, "report")
        self.assertEqual(
            self.client.post(self.url + "/questions", json={"text": "Why?"}).status_code, 204
        )
        self.engine.append_question.assert_awaited_once_with(self.match_id, "Why?")
        self.assertEqual(
            self.client.patch(self.url + "/status", json={"status": "APPLIED"}).status_code, 200
        )
        self.engine.update_pipeline_status.assert_awaited_once_with(self.match_id, "APPLIED")

    def test_discussion_identity_history_and_conflicts(self):
        self.assertEqual(self.client.get(self.url + "/discussion").status_code, 200)
        self.assertEqual(self.client.post(self.url + "/discussion/start").status_code, 204)
        self.discussion.start.assert_awaited_once_with(self.job, 123, 123)
        self.assertEqual(
            self.client.post(
                self.url + "/discussion/messages", json={"text": "Hi", "confirmed": True}
            ).status_code,
            200,
        )
        self.discussion.send.assert_awaited_with(self.match_id, 123, 123, "Hi", confirmed=True)
        self.assertEqual(
            self.client.post(
                self.url + "/discussion/materials", json={"material_path": self.material.id}
            ).status_code,
            200,
        )
        self.discussion.send.assert_awaited_with(
            self.match_id,
            123,
            123,
            self.material.text,
            confirmed=True,
            material_path=self.material.id,
        )
        self.assertEqual(self.client.post(self.url + "/discussion/finish").status_code, 204)
        self.discussion.start.side_effect = ValueError("Reopening is not supported.")
        response = self.client.post(self.url + "/discussion/start")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Reopening", response.json()["detail"])

    def test_material_filter_and_cors(self):
        self.assertEqual(len(self.client.get("/api/materials?q=EXAMPLE").json()), 1)
        self.assertEqual(self.client.get("/api/materials?q=missing").json(), [])
        response = self.client.options(
            self.url,
            headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
        )
        self.assertEqual(response.headers["access-control-allow-origin"], "http://localhost:5173")


if __name__ == "__main__":
    unittest.main()
