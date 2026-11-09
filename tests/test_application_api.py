"""Application HTTP contracts; mocked services never touch storage or providers."""

import unittest
from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4

from litestar.testing import TestClient

from src.api import (
    ApplicationGenerationException,
    ApplicationQuestionResponse,
    ApplicationResponse,
    ApplicationService,
    ApplicationsResponse,
    NotFoundException,
    create_app,
)


class ApplicationApiTests(unittest.TestCase):
    def setUp(self):
        self.match_id = uuid4()
        self.collection = f"/api/matches/{self.match_id}/applications"
        self.url = "/api/applications/7"
        self.draft = ApplicationResponse(
            id=7,
            match_id=self.match_id,
            job_id="synthetic:job",
            cover_letter="  Authored letter\n",
            questions=[ApplicationQuestionResponse(question="Why?", answer=None)],
            created_at=datetime(2026, 10, 24, 12, tzinfo=timezone.utc),
            updated_at=None,
        )
        self.service = Mock(spec=ApplicationService)
        self.service.create_application.return_value = self.draft
        self.service.list_applications.return_value = ApplicationsResponse(
            applications=[self.draft], total=123, limit=50, offset=0
        )
        self.service.get_application.return_value = self.draft
        self.service.update_application.return_value = self.draft
        self.service.regenerate_application.return_value = self.draft
        self.client = TestClient(
            create_app(application_service=self.service), raise_server_exceptions=False
        )

    def requests(self):
        return [
            ("POST", self.collection, {"questions": ["Why?"]}, "create_application"),
            ("GET", self.collection, None, "list_applications"),
            ("GET", self.url, None, "get_application"),
            ("PATCH", self.url, {"cover_letter": "Edited"}, "update_application"),
            ("POST", self.url + "/generation", {"questions": ["Why?"]}, "regenerate_application"),
        ]

    def test_create_and_retrieve_saved_drafts(self):
        first = self.client.post(self.collection, json={"questions": ["Why?"]})
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.json(), self.draft.model_dump(mode="json"))
        self.assertEqual(
            set(first.json()),
            {
                "id",
                "match_id",
                "job_id",
                "cover_letter",
                "questions",
                "created_at",
                "updated_at",
            },
        )
        self.assertEqual(first.json()["questions"], [{"question": "Why?", "answer": None}])
        self.assertEqual(first.json()["created_at"], "2026-10-24T12:00:00Z")
        self.assertIsNone(first.json()["updated_at"])
        self.service.create_application.return_value = self.draft.model_copy(update={"id": 8})
        second = self.client.post(self.collection, json={"questions": ["Why?"]})
        self.assertEqual(second.status_code, 201)
        self.assertEqual(second.json()["id"], 8)
        detail = self.client.get(self.url)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json(), first.json())
        self.service.get_application.assert_awaited_once_with(7)

    def test_generation_selectors_and_supplied_questions(self):
        cases = [
            ({"generate_answers": False}, True, False, []),
            ({"generate_cover_letter": False, "questions": ["  Why?  "]}, False, True, ["Why?"]),
            ({"questions": ["Why?", "How?"]}, True, True, ["Why?", "How?"]),
        ]
        for url, name, identifier, status in [
            (self.collection, "create_application", self.match_id, 201),
            (self.url + "/generation", "regenerate_application", 7, 200),
        ]:
            for body, letter, answers, questions in cases:
                with self.subTest(url=url, body=body):
                    response = self.client.post(url, json=body)
                    self.assertEqual(response.status_code, status)
                    args = getattr(self.service, name).call_args.args
                    self.assertEqual(args[0], identifier)
                    self.assertEqual(
                        args[1].model_dump(),
                        {
                            "generate_cover_letter": letter,
                            "generate_answers": answers,
                            "questions": questions,
                        },
                    )
        # Maximum question length is accepted for answer generation.
        response = self.client.post(self.collection, json={"questions": ["x" * 16000]})
        self.assertEqual(response.status_code, 201)

    def test_list_defaults_totals_order_and_empty_pages(self):
        newer = self.draft.model_copy(update={"id": 8})
        self.service.list_applications.return_value = ApplicationsResponse(
            applications=[newer, self.draft], total=123, limit=50, offset=0
        )
        response = self.client.get(self.collection)
        self.assertEqual(response.status_code, 200)
        self.service.list_applications.assert_awaited_once_with(self.match_id, limit=50, offset=0)
        self.assertEqual([a["id"] for a in response.json()["applications"]], [8, 7])
        self.assertEqual(response.json()["total"], 123)
        for limit, offset, total in [(1, 0, 0), (250, 1000, 123)]:
            self.service.list_applications.return_value = ApplicationsResponse(
                applications=[], total=total, limit=limit, offset=offset
            )
            response = self.client.get(f"{self.collection}?limit={limit}&offset={offset}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.json(),
                {
                    "applications": [],
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                },
            )
            self.service.list_applications.assert_awaited_with(
                self.match_id, limit=limit, offset=offset
            )

    def test_patch_preserves_omission_null_empty_and_authored_text(self):
        cases = [
            {"cover_letter": "  Edited letter\n"},
            {"cover_letter": None},
            {"questions": []},
            {"questions": [{"question": "  Why? ", "answer": "  Authored answer\n"}]},
            {"cover_letter": "", "questions": [{"question": "How?"}]},
        ]
        for body in cases:
            with self.subTest(body=body):
                response = self.client.patch(self.url, json=body)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), self.draft.model_dump(mode="json"))
                identifier, patch = self.service.update_application.call_args.args
                self.assertEqual(identifier, 7)
                self.assertEqual(patch.model_fields_set, set(body))
                forwarded = patch.model_dump(exclude_unset=True)
                self.assertEqual(set(forwarded), set(body))
                if "cover_letter" in body:
                    self.assertEqual(forwarded["cover_letter"], body["cover_letter"])
                if body.get("questions"):
                    self.assertEqual(
                        forwarded["questions"][0]["question"],
                        body["questions"][0]["question"].strip(),
                    )
                    if "answer" in body["questions"][0]:
                        self.assertEqual(
                            forwarded["questions"][0]["answer"], body["questions"][0]["answer"]
                        )

    def test_regeneration_returns_same_draft_identity(self):
        self.service.regenerate_application.return_value = self.draft.model_copy(
            update={
                "cover_letter": "Regenerated",
                "updated_at": datetime(2026, 10, 24, 13, tzinfo=timezone.utc),
            }
        )
        response = self.client.post(self.url + "/generation", json={"generate_answers": False})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], 7)
        self.assertEqual(
            response.json()["questions"], self.draft.model_dump(mode="json")["questions"]
        )
        self.assertEqual(response.json()["updated_at"], "2026-10-24T13:00:00Z")

    def test_invalid_inputs_do_not_call_service(self):
        for query in ["limit=0", "limit=251", "limit=no", "offset=-1", "offset=1.5"]:
            self.assertEqual(self.client.get(f"{self.collection}?{query}").status_code, 422)
        for method, url, body, _ in self.requests():
            invalid_urls = (
                [url.replace(str(self.match_id), "invalid")]
                if str(self.match_id) in url
                else [url.replace("/7", f"/{value}") for value in ["0", "-1", "x", "1.5"]]
            )
            for invalid in invalid_urls:
                self.assertEqual(self.client.request(method, invalid, json=body).status_code, 422)
        invalid_generation = [
            {},
            {"generate_cover_letter": False, "generate_answers": False},
            {"questions": []},
            {"questions": [" "]},
            {"questions": ["x" * 16001]},
            {"questions": None},
            {"questions": ["Why?"], "match_id": str(self.match_id)},
        ]
        for url in [self.collection, self.url + "/generation"]:
            for body in invalid_generation:
                self.assertEqual(self.client.post(url, json=body).status_code, 422)
        for body in [
            {},
            {"questions": None},
            {"questions": [{"question": " "}]},
            {"questions": [{"question": "x" * 16001}]},
            {"questions": [{"question": "Why?", "extra": True}]},
            {"extra": True},
        ]:
            self.assertEqual(self.client.patch(self.url, json=body).status_code, 422)
        for _, _, _, name in self.requests():
            getattr(self.service, name).assert_not_awaited()

    def test_unknown_resources_and_unconfigured_service(self):
        empty = TestClient(create_app())
        for method, url, body, name in self.requests():
            with self.subTest(method=method, url=url):
                response = empty.request(method, url, json=body)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(
                    response.json(), {"detail": "Application service is not configured."}
                )
                getattr(self.service, name).side_effect = NotFoundException("Private details")
                response = self.client.request(method, url, json=body)
                self.assertEqual(response.status_code, 404)
                detail = "Match not found." if url == self.collection else "Application not found."
                self.assertEqual(response.json(), {"detail": detail})

    def test_generation_failure_is_safe(self):
        for url, name in [
            (self.collection, "create_application"),
            (self.url + "/generation", "regenerate_application"),
        ]:
            getattr(self.service, name).side_effect = ApplicationGenerationException(
                "Private provider data"
            )
            response = self.client.post(url, json={"questions": ["Why?"]})
            self.assertEqual(response.status_code, 502)
            self.assertEqual(response.json(), {"detail": "Application generation failed."})

    def test_unexpected_errors_are_generic_logged_500(self):
        for error_type in [ValueError, RuntimeError]:
            for method, url, body, name in self.requests():
                with self.subTest(url=url, error_type=error_type), self.assertLogs(level="ERROR"):
                    getattr(self.service, name).side_effect = error_type("Private details")
                    response = self.client.request(method, url, json=body)
                    self.assertEqual(response.status_code, 500)
                    self.assertEqual(
                        response.json(), {"detail": "Request failed. Check server logs."}
                    )

    def test_openapi_contract(self):
        schema = self.client.get("/openapi.json").json()
        paths = schema["paths"]
        collection = paths["/api/matches/{id}/applications"]
        self.assertEqual(set(collection), {"post", "get"})
        self.assertIn("201", collection["post"]["responses"])
        self.assertEqual(set(paths["/api/applications/{id}"]), {"get", "patch"})
        self.assertIn("502", paths["/api/applications/{id}/generation"]["post"]["responses"])
        models = schema["components"]["schemas"]
        self.assertFalse(models["GenerationInput"]["additionalProperties"])
        self.assertFalse(models["ApplicationPatchInput"]["additionalProperties"])
        self.assertEqual(
            set(models["ApplicationResponse"]["properties"]),
            {
                "id",
                "match_id",
                "job_id",
                "cover_letter",
                "questions",
                "created_at",
                "updated_at",
            },
        )
        self.assertEqual(
            models["ApplicationPatchInput"]["properties"]["questions"]["type"], "array"
        )
