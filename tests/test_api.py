"""Job HTTP contracts with synthetic services; no database or provider calls."""

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

from litestar.testing import TestClient

from src.api import JobResponse, JobService, JobsResponse, NotFoundException, create_app


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.job = JobResponse(
            id="synthetic:job-123",
            title="Engineer",
            company="Synthetic Company",
            location=None,
            description="Synthetic job",
            url="https://example.com/jobs/123",
            apply_url=None,
            ats_type="custom",
            employment_type=None,
            questions=["Why this role?"],
            salary_min=None,
            salary_max=None,
            salary_currency=None,
            posted_at=datetime(2026, 10, 23, 12, tzinfo=timezone.utc),
            fetched_at=None,
        )
        self.service = Mock(spec=JobService)
        self.service.list_jobs = AsyncMock(
            return_value=JobsResponse(jobs=[self.job], total=123, limit=50, offset=0)
        )
        self.service.get_job_by_id = AsyncMock(return_value=self.job)
        self.app = create_app(self.service)
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def test_list_defaults_and_total(self) -> None:
        response = self.client.get("/api/jobs")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"jobs", "total", "limit", "offset"})
        self.assertEqual((body["total"], body["limit"], body["offset"]), (123, 50, 0))
        self.assertEqual(body["jobs"], [self.client.get("/api/jobs/synthetic:job-123").json()])
        self.service.list_jobs.assert_awaited_once_with(limit=50, offset=0)

    def test_explicit_pagination_and_empty_page(self) -> None:
        for limit, offset in [(1, 0), (75, 10), (250, 1000)]:
            with self.subTest(limit=limit, offset=offset):
                self.service.list_jobs.return_value = JobsResponse(
                    jobs=[], total=123, limit=limit, offset=offset
                )
                response = self.client.get(f"/api/jobs?limit={limit}&offset={offset}")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.json(),
                    {"jobs": [], "total": 123, "limit": limit, "offset": offset},
                )
                self.service.list_jobs.assert_awaited_with(limit=limit, offset=offset)

    def test_invalid_pagination_never_calls_service(self) -> None:
        for query in ["limit=0", "limit=-1", "limit=251", "limit=no", "offset=-1", "offset=1.5"]:
            with self.subTest(query=query):
                self.assertEqual(self.client.get(f"/api/jobs?{query}").status_code, 422)
        self.service.list_jobs.assert_not_awaited()

    def test_detail_uses_opaque_string_id_and_snake_case(self) -> None:
        response = self.client.get("/api/jobs/synthetic:job-123")
        self.assertEqual(response.status_code, 200)
        self.service.get_job_by_id.assert_awaited_once_with("synthetic:job-123")
        body = response.json()
        self.assertEqual(
            set(body),
            {
                "id",
                "title",
                "company",
                "location",
                "description",
                "url",
                "apply_url",
                "ats_type",
                "employment_type",
                "questions",
                "salary_min",
                "salary_max",
                "salary_currency",
                "posted_at",
                "fetched_at",
            },
        )
        self.assertEqual(body["id"], self.job.id)
        self.assertEqual(body["questions"], ["Why this role?"])
        self.assertEqual(body["posted_at"], "2026-10-23T12:00:00Z")
        for field in [
            "location",
            "apply_url",
            "employment_type",
            "salary_min",
            "salary_max",
            "salary_currency",
            "fetched_at",
        ]:
            self.assertIsNone(body[field])
        self.service.get_job_by_id.return_value = self.job.model_copy(
            update={"company": None, "posted_at": None}
        )
        nullable = self.client.get("/api/jobs/synthetic:job-123").json()
        self.assertIsNone(nullable["company"])
        self.assertIsNone(nullable["posted_at"])

    def test_missing_job(self) -> None:
        self.service.get_job_by_id.side_effect = NotFoundException("Internal record details")
        response = self.client.get("/api/jobs/missing")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Job not found."})

    def test_unconfigured_service(self) -> None:
        client = TestClient(create_app())
        for url in ["/api/jobs", "/api/jobs/synthetic:job-123"]:
            with self.subTest(url=url):
                response = client.get(url)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {"detail": "Job service is not configured."})
        self.assertEqual(client.get("/api/jobs?limit=251").status_code, 422)

    def test_unexpected_errors_are_logged_and_hidden(self) -> None:
        self.service.list_jobs.side_effect = RuntimeError("Private database details")
        self.service.get_job_by_id.side_effect = RuntimeError("Private database details")
        for url in ["/api/jobs", "/api/jobs/synthetic:job-123"]:
            with self.subTest(url=url), self.assertLogs("src.api.app", level="ERROR"):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 500)
                self.assertEqual(response.json(), {"detail": "Request failed. Check server logs."})

    def test_openapi_routes_models_and_pagination(self) -> None:
        schema = self.client.get("/openapi.json").json()
        self.assertEqual(
            set(schema["paths"]),
            {
                "/api/jobs",
                "/api/jobs/scrape",
                "/api/jobs/{id}",
                "/api/matches",
                "/api/matches/by-job/{job_id}",
                "/api/matches/counts",
                "/api/matches/{id}",
                "/api/matches/{id}/debug",
                "/api/matches/{id}/reports",
                "/api/matches/{id}/status",
                "/api/matches/{id}/discussion",
                "/api/matches/{id}/discussion/start",
                "/api/matches/{id}/discussion/messages",
                "/api/matches/{id}/discussion/stream",
                "/api/matches/{id}/discussion/finish",
                "/api/matches/{id}/applications",
                "/api/applications/{id}",
                "/api/applications/{id}/generation",
            },
        )
        properties = schema["components"]["schemas"]["JobResponse"]["properties"]
        self.assertIn("salary_min", properties)
        self.assertIn("posted_at", properties)
        self.assertFalse(any("-" in name for name in properties))
        parameters = {
            p["name"]: p["schema"] for p in schema["paths"]["/api/jobs"]["get"]["parameters"]
        }
        self.assertEqual(set(parameters), {"limit", "offset"})
        self.assertEqual(parameters["limit"]["default"], 50)
        self.assertEqual(parameters["limit"]["minimum"], 1)
        self.assertEqual(parameters["limit"]["maximum"], 250)
        self.assertEqual(parameters["offset"]["minimum"], 0)
        for path in schema["paths"].values():
            for operation in path.values():
                self.assertIn("503", operation["responses"])
        self.assertIn("404", schema["paths"]["/api/jobs/{id}"]["get"]["responses"])

    def test_old_routes_are_removed(self) -> None:
        match = "/api/matches/00000000-0000-0000-0000-000000000001"
        routes = [
            ("POST", match + "/questions"),
            ("POST", match + "/generation"),
            ("GET", "/api/materials"),
            ("POST", match + "/discussion/materials"),
        ]
        for method, url in routes:
            with self.subTest(method=method, url=url):
                self.assertEqual(self.client.request(method, url).status_code, 404)

    def test_cors_default_and_custom_origins(self) -> None:
        for origins, origin in [
            (None, "http://localhost:5173"),
            (["https://example.com"], "https://example.com"),
        ]:
            with self.subTest(origin=origin):
                client = TestClient(create_app(allowed_origins=origins))
                response = client.options(
                    "/api/jobs",
                    headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
                )
                self.assertEqual(response.status_code, 204)
                self.assertEqual(response.headers["access-control-allow-origin"], origin)
