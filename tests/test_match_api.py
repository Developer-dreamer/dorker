"""Match HTTP contracts with synthetic services; no persistence or model calls."""

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from litestar.testing import TestClient

from src.api import (
    MatchAnalytics,
    MatchesResponse,
    MatchResponse,
    MatchService,
    NotFoundException,
    PipelineStatus,
    create_app,
)
from src.shared.models.match import SuitabilityTier


class MatchApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.match_id = uuid4()
        self.match = MatchResponse(
            id=self.match_id,
            job_id="synthetic:job",
            suitability_tier=SuitabilityTier.SUITABLE,
            pipeline_status=PipelineStatus.PENDING,
            technical_capability_score=0.8,
            strategic_value_score=0.9,
            analytics=MatchAnalytics(summary="Synthetic summary", pros=["Opportunity"], cons=[]),
            rejection_reason=None,
            created_at=datetime(2026, 10, 23, 12, tzinfo=timezone.utc),
        )
        self.service = Mock(spec=MatchService)
        self.service.list_matches = AsyncMock(
            return_value=MatchesResponse(matches=[self.match], total=123, limit=50, offset=0)
        )
        self.service.get_match_by_id = AsyncMock(return_value=self.match)
        self.service.get_counts = AsyncMock(return_value={SuitabilityTier.SUITABLE: 5})
        self.service.get_debug = AsyncMock(return_value='{"synthetic": true}')
        self.service.report = AsyncMock()
        self.service.update_pipeline_status = AsyncMock()
        self.client = TestClient(
            create_app(match_service=self.service), raise_server_exceptions=False
        )
        self.url = f"/api/matches/{self.match_id}"

    def record_requests(self) -> list[tuple[str, str, dict[str, str] | None, str]]:
        return [
            ("GET", self.url, None, "get_match_by_id"),
            ("GET", self.url + "/debug", None, "get_debug"),
            ("POST", self.url + "/reports", {"text": "Synthetic report"}, "report"),
            ("PATCH", self.url + "/status", {"status": "APPLIED"}, "update_pipeline_status"),
        ]

    def test_default_list_and_detail_contract(self) -> None:
        response = self.client.get("/api/matches")
        self.assertEqual(response.status_code, 200)
        self.service.list_matches.assert_awaited_once_with(
            limit=50, offset=0, tiers=None, pipeline_status=None
        )
        body = response.json()
        self.assertEqual(set(body), {"matches", "total", "limit", "offset"})
        self.assertEqual((body["total"], body["limit"], body["offset"]), (123, 50, 0))
        detail = self.client.get(self.url)
        self.assertEqual(detail.status_code, 200)
        self.service.get_match_by_id.assert_awaited_once_with(self.match_id)
        self.assertEqual(body["matches"], [detail.json()])
        self.assertEqual(
            detail.json(),
            {
                "id": str(self.match_id),
                "job_id": "synthetic:job",
                "suitability_tier": "SUITABLE",
                "pipeline_status": "PENDING",
                "technical_capability_score": 0.8,
                "strategic_value_score": 0.9,
                "analytics": {"summary": "Synthetic summary", "pros": ["Opportunity"], "cons": []},
                "rejection_reason": None,
                "created_at": "2026-10-23T12:00:00Z",
            },
        )

    def test_pagination_and_repeated_filters(self) -> None:
        for limit, offset in [(1, 0), (250, 1000)]:
            with self.subTest(limit=limit, offset=offset):
                self.service.list_matches.return_value = MatchesResponse(
                    matches=[], total=123, limit=limit, offset=offset
                )
                response = self.client.get(
                    f"/api/matches?limit={limit}&offset={offset}"
                    "&tiers=SUITABLE&tiers=REJECTED&pipeline_status=PENDING&pipeline_status=OFFER"
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.json(),
                    {
                        "matches": [],
                        "total": 123,
                        "limit": limit,
                        "offset": offset,
                    },
                )
                self.service.list_matches.assert_awaited_with(
                    limit=limit,
                    offset=offset,
                    tiers=[SuitabilityTier.SUITABLE, SuitabilityTier.REJECTED],
                    pipeline_status=[PipelineStatus.PENDING, PipelineStatus.OFFER],
                )

    def test_nullable_analytics(self) -> None:
        self.service.get_match_by_id.return_value = self.match.model_copy(
            update={"analytics": None, "rejection_reason": "Synthetic rejection"}
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["analytics"])
        self.assertEqual(response.json()["rejection_reason"], "Synthetic rejection")

    def test_counts_static_route_filters_and_zero_tiers(self) -> None:
        response = self.client.get("/api/matches/counts")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"SUITABLE": 5, "RUNWAY": 0, "STRETCH": 0, "REJECTED": 0})
        self.service.get_counts.assert_awaited_once_with(pipeline_status=None)
        self.service.get_match_by_id.assert_not_awaited()
        self.client.get("/api/matches/counts?pipeline_status=APPLIED&pipeline_status=SKIPPED")
        self.service.get_counts.assert_awaited_with(
            pipeline_status=[PipelineStatus.APPLIED, PipelineStatus.SKIPPED]
        )
        self.service.get_counts.return_value = {}
        self.assertEqual(
            self.client.get("/api/matches/counts").json(),
            {
                "SUITABLE": 0,
                "RUNWAY": 0,
                "STRETCH": 0,
                "REJECTED": 0,
            },
        )

    def test_debug_string_and_null(self) -> None:
        response = self.client.get(self.url + "/debug")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"debug": '{"synthetic": true}'})
        self.service.get_debug.assert_awaited_once_with(self.match_id)
        self.service.get_debug.return_value = None
        self.assertEqual(self.client.get(self.url + "/debug").json(), {"debug": None})

    def test_report_trimming_length_boundary_and_no_response_body(self) -> None:
        response = self.client.post(self.url + "/reports", json={"text": "  Synthetic report  "})
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        self.service.report.assert_awaited_once_with(self.match_id, "Synthetic report")
        response = self.client.post(self.url + "/reports", json={"text": "x" * 16000})
        self.assertEqual(response.status_code, 204)
        self.service.report.assert_awaited_with(self.match_id, "x" * 16000)

    def test_all_pipeline_statuses(self) -> None:
        expected = {
            "PENDING",
            "APPLIED",
            "GHOSTED",
            "INTERVIEWING",
            "REJECTED_EARLY",
            "REJECTED_LATE",
            "DECLINED",
            "OFFER",
            "IGNORED",
            "SKIPPED",
        }
        self.assertEqual({status.value for status in PipelineStatus}, expected)
        for status in PipelineStatus:
            with self.subTest(status=status):
                response = self.client.patch(self.url + "/status", json={"status": status.value})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"status": status.value})
                self.service.update_pipeline_status.assert_awaited_with(self.match_id, status)

    def test_invalid_inputs_do_not_call_service(self) -> None:
        for query in [
            "limit=0",
            "limit=251",
            "limit=no",
            "offset=-1",
            "offset=1.5",
            "tiers=UNKNOWN",
            "pipeline_status=UNKNOWN",
        ]:
            with self.subTest(query=query):
                self.assertEqual(self.client.get(f"/api/matches?{query}").status_code, 422)
        self.assertEqual(
            self.client.get("/api/matches/counts?pipeline_status=UNKNOWN").status_code, 422
        )
        for method, url, body, _ in self.record_requests():
            self.assertEqual(
                self.client.request(
                    method, url.replace(str(self.match_id), "invalid"), json=body
                ).status_code,
                422,
            )
        for body in [{"text": " "}, {"text": "x" * 16001}, {"text": "ok", "extra": True}, {}]:
            self.assertEqual(self.client.post(self.url + "/reports", json=body).status_code, 422)
        for body in [{"status": "BOGUS"}, {"status": "APPLIED", "extra": True}, {}]:
            self.assertEqual(self.client.patch(self.url + "/status", json=body).status_code, 422)
        for name in [
            "list_matches",
            "get_counts",
            "get_match_by_id",
            "get_debug",
            "report",
            "update_pipeline_status",
        ]:
            getattr(self.service, name).assert_not_awaited()

    def test_record_operations_translate_not_found(self) -> None:
        for method, url, body, service_method in self.record_requests():
            with self.subTest(method=method, url=url):
                getattr(self.service, service_method).side_effect = NotFoundException(
                    "Private data"
                )
                response = self.client.request(method, url, json=body)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json(), {"detail": "Match not found."})

    def test_unconfigured_service(self) -> None:
        client = TestClient(create_app())
        requests = [
            ("GET", "/api/matches", None, "list_matches"),
            ("GET", "/api/matches/counts", None, "get_counts"),
            *self.record_requests(),
        ]
        for method, url, body, _ in requests:
            with self.subTest(method=method, url=url):
                response = client.request(method, url, json=body)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {"detail": "Match service is not configured."})

    def test_unexpected_errors_are_logged_and_hidden(self) -> None:
        requests = [
            ("GET", "/api/matches", None, "list_matches"),
            ("GET", "/api/matches/counts", None, "get_counts"),
            *self.record_requests(),
        ]
        for method, url, body, service_method in requests:
            with (
                self.subTest(method=method, url=url),
                self.assertLogs("src.api.app", level="ERROR"),
            ):
                getattr(self.service, service_method).side_effect = RuntimeError("Private details")
                response = self.client.request(method, url, json=body)
                self.assertEqual(response.status_code, 500)
                self.assertEqual(response.json(), {"detail": "Request failed. Check server logs."})

    def test_openapi_models_filters_and_methods(self) -> None:
        schema = self.client.get("/openapi.json").json()
        paths = schema["paths"]
        self.assertNotIn("/api/matches/next", paths)
        self.assertEqual(set(paths["/api/matches/{id}/reports"]), {"post"})
        self.assertEqual(set(paths["/api/matches/{id}/status"]), {"patch"})
        models = schema["components"]["schemas"]
        properties = models["MatchResponse"]["properties"]
        self.assertIn("suitability_tier", properties)
        self.assertFalse(any("-" in name for name in properties))
        self.assertEqual(models["MatchAnalytics"]["properties"]["pros"]["type"], "array")
        self.assertEqual(models["MatchAnalytics"]["properties"]["cons"]["type"], "array")
        parameters = paths["/api/matches"]["get"]["parameters"]
        self.assertEqual(
            {p["name"] for p in parameters}, {"limit", "offset", "tiers", "pipeline_status"}
        )
        counts_parameters = paths["/api/matches/counts"]["get"]["parameters"]
        self.assertEqual([p["name"] for p in counts_parameters], ["pipeline_status"])
