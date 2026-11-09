"""Discussion adapter contracts; synthetic services never call storage or models."""

import asyncio
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4

from litestar.testing import TestClient

from src.api import (
    DiscussionAcceptedEvent,
    DiscussionConflictException,
    DiscussionDeltaEvent,
    DiscussionDoneEvent,
    DiscussionMessageResponse,
    DiscussionReplyException,
    DiscussionReplyResponse,
    DiscussionResponse,
    DiscussionService,
    DiscussionState,
    NotFoundException,
    create_app,
)
from src.api.handlers.discussion import _reply_events


def message(role: str = "user") -> DiscussionMessageResponse:
    return DiscussionMessageResponse.model_validate(
        {
            "id": uuid4(),
            "role": role,
            "content": "Synthetic conversation",
            "created_at": datetime(2026, 10, 23, 12, tzinfo=timezone.utc),
        }
    )


class DiscussionApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.match_id = uuid4()
        self.url = f"/api/matches/{self.match_id}/discussion"
        self.user = message()
        self.assistant = message("assistant")
        self.service = Mock(spec=DiscussionService)
        self.service.get_discussion.return_value = DiscussionResponse(
            state=DiscussionState.OPEN, messages=[self.user, self.assistant]
        )
        self.service.send_message.return_value = DiscussionReplyResponse(
            user_message=self.user, assistant_message=self.assistant
        )
        self.closed = False

        async def stream():
            try:
                yield DiscussionAcceptedEvent(message=self.user)
                yield DiscussionDeltaEvent(text="Synthetic ")
                yield DiscussionDeltaEvent(text="conversation")
                yield DiscussionDoneEvent(message=self.assistant)
            finally:
                self.closed = True

        self.service.stream_message.side_effect = lambda *args: stream()
        self.client = TestClient(
            create_app(discussion_service=self.service), raise_server_exceptions=False
        )

    def requests(self):
        return [
            ("GET", self.url, None, "get_discussion"),
            ("POST", self.url + "/start", None, "start"),
            ("POST", self.url + "/messages", {"text": "Hello"}, "send_message"),
            ("POST", self.url + "/stream", {"text": "Hello"}, "stream_message"),
            ("POST", self.url + "/finish", None, "finish"),
        ]

    def test_history_states_visible_shape_and_order(self):
        for state, messages in [
            (DiscussionState.NOT_STARTED, []),
            (DiscussionState.OPEN, [self.user, self.assistant]),
            (DiscussionState.FINISHED, [self.user, self.assistant]),
        ]:
            with self.subTest(state=state):
                self.service.get_discussion.return_value = DiscussionResponse(
                    state=state, messages=messages
                )
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.json(),
                    {
                        "state": state.value,
                        "messages": [m.model_dump(mode="json") for m in messages],
                    },
                )
                self.service.get_discussion.assert_awaited_with(self.match_id)
        body = response.json()["messages"][0]
        self.assertEqual(set(body), {"id", "role", "content", "created_at"})
        self.assertEqual(body["created_at"], "2026-10-23T12:00:00Z")

    def test_initialization_and_finish_delegate_without_identity(self):
        for suffix, method in [("start", self.service.start), ("finish", self.service.finish)]:
            response = self.client.post(f"{self.url}/{suffix}")
            self.assertEqual(response.status_code, 204)
            self.assertEqual(response.content, b"")
            method.assert_awaited_once_with(self.match_id)

    def test_regular_reply_trimmed_and_persisted_messages(self):
        response = self.client.post(self.url + "/messages", json={"text": "  Hello  "})
        self.assertEqual(response.status_code, 200)
        self.service.send_message.assert_awaited_once_with(self.match_id, "Hello")
        self.assertEqual(
            response.json(),
            {
                "user_message": self.user.model_dump(mode="json"),
                "assistant_message": self.assistant.model_dump(mode="json"),
            },
        )
        response = self.client.post(self.url + "/messages", json={"text": "x" * 16000})
        self.assertEqual(response.status_code, 200)

    def test_stream_event_order_and_generator_cleanup(self):
        response = self.client.post(self.url + "/stream", json={"text": "  Hello  "})
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/x-ndjson", response.headers["content-type"])
        self.assertEqual(response.headers["cache-control"], "no-cache")
        self.assertEqual(response.headers["x-accel-buffering"], "no")
        self.service.stream_message.assert_awaited_once_with(self.match_id, "Hello")
        events = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual([e["type"] for e in events], ["accepted", "delta", "delta", "done"])
        self.assertEqual(events[0]["message"], self.user.model_dump(mode="json"))
        self.assertEqual(events[-1]["message"], self.assistant.model_dump(mode="json"))
        self.assertTrue(self.closed)

    def test_validation_never_calls_services(self):
        for method, url, body, _ in self.requests():
            self.assertEqual(
                self.client.request(
                    method, url.replace(str(self.match_id), "invalid"), json=body
                ).status_code,
                422,
            )
        for suffix in ["messages", "stream"]:
            for body in [
                {},
                {"text": " "},
                {"text": "x" * 16001},
                {"text": "ok", "user_id": 1},
                {"text": "ok", "confirmed": True},
                {"text": "ok", "attachments": []},
            ]:
                with self.subTest(suffix=suffix, body_keys=list(body)):
                    self.assertEqual(
                        self.client.post(f"{self.url}/{suffix}", json=body).status_code, 422
                    )
        for _, _, _, name in self.requests():
            getattr(self.service, name).assert_not_awaited()

    def test_not_found_and_unconfigured_services(self):
        empty_client = TestClient(create_app())
        for method, url, body, name in self.requests():
            with self.subTest(url=url):
                response = empty_client.request(method, url, json=body)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(
                    response.json(), {"detail": "Discussion service is not configured."}
                )
                getattr(self.service, name).side_effect = NotFoundException("Private record")
                response = self.client.request(method, url, json=body)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json(), {"detail": "Match not found."})

    def test_lifecycle_conflicts_are_http_errors_before_streaming(self):
        for method, url, body, name in self.requests()[1:]:
            for detail in ["No discussion started.", "Discussion already finished."]:
                with self.subTest(url=url, detail=detail):
                    getattr(self.service, name).side_effect = DiscussionConflictException(detail)
                    response = self.client.request(method, url, json=body)
                    self.assertEqual(response.status_code, 409)
                    self.assertEqual(response.json(), {"detail": detail})
        self.service.start.side_effect = DiscussionConflictException("Discussion already started.")
        self.assertEqual(self.client.post(self.url + "/start").status_code, 409)

    def test_known_reply_failure_reports_saved_before_headers(self):
        for suffix, name in [("messages", "send_message"), ("stream", "stream_message")]:
            for saved in [False, True]:
                with self.subTest(suffix=suffix, saved=saved):
                    error = DiscussionReplyException(saved=saved)
                    getattr(self.service, name).side_effect = error
                    response = self.client.post(f"{self.url}/{suffix}", json={"text": "Hello"})
                    self.assertEqual(response.status_code, 502)
                    self.assertEqual(response.json(), {"detail": str(error), "saved": saved})

    def test_unexpected_preflight_failures_are_generic_logged_500(self):
        for error_type in [RuntimeError, ValueError]:
            for method, url, body, name in self.requests():
                with self.subTest(url=url, error_type=error_type), self.assertLogs(level="ERROR"):
                    getattr(self.service, name).side_effect = error_type("Private provider details")
                    response = self.client.request(method, url, json=body)
                    self.assertEqual(response.status_code, 500)
                    self.assertEqual(
                        response.json(), {"detail": "Request failed. Check server logs."}
                    )

    def test_stream_failures_before_and_after_acceptance(self):
        for accepted in [False, True]:
            for known in [False, True]:

                async def failed(*args, accepted=accepted, known=known):
                    try:
                        if accepted:
                            yield DiscussionAcceptedEvent(message=self.user)
                            yield DiscussionDeltaEvent(text="Partial")
                        if known:
                            raise DiscussionReplyException(saved=accepted)
                        raise RuntimeError("Private provider details")
                    finally:
                        self.closed = True

                self.closed = False
                self.service.stream_message.side_effect = lambda *args: failed()
                if known:
                    response = self.client.post(self.url + "/stream", json={"text": "Hi"})
                else:
                    with self.assertLogs("src.api.handlers.discussion", level="ERROR"):
                        response = self.client.post(self.url + "/stream", json={"text": "Hi"})
                self.assertEqual(response.status_code, 200)
                events = [json.loads(line) for line in response.text.splitlines()]
                self.assertEqual(
                    events[-1],
                    {
                        "type": "error",
                        "detail": "Reply failed. Reload the discussion before retrying.",
                        "saved": accepted,
                    },
                )
                self.assertTrue(self.closed)
                self.assertNotIn("Private", response.text)
                self.assertNotIn("done", [e["type"] for e in events])

    def test_stream_failure_can_report_persistence_before_accepted_event(self):
        async def failed():
            raise DiscussionReplyException(saved=True)
            yield DiscussionDeltaEvent(text="unreachable")

        self.service.stream_message.side_effect = lambda *args: failed()
        response = self.client.post(self.url + "/stream", json={"text": "Hello"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [json.loads(line) for line in response.text.splitlines()],
            [
                {
                    "type": "error",
                    "detail": "Reply failed. Reload the discussion before retrying.",
                    "saved": True,
                }
            ],
        )

    def test_openapi_contract(self):
        schema = self.client.get("/openapi.json").json()
        paths = schema["paths"]
        base = "/api/matches/{id}/discussion"
        self.assertEqual(set(paths[base]), {"get"})
        for suffix in ["start", "messages", "stream", "finish"]:
            operation = paths[f"{base}/{suffix}"]["post"]
            self.assertIn("409", operation["responses"])
            self.assertIn("503", operation["responses"])
        content = paths[f"{base}/stream"]["post"]["responses"]["200"]["content"]
        self.assertEqual(set(content), {"application/x-ndjson"})
        models = schema["components"]["schemas"]
        self.assertEqual(
            set(models["DiscussionMessageResponse"]["properties"]),
            {"id", "role", "content", "created_at"},
        )
        self.assertFalse(models["DiscussionMessageInput"]["additionalProperties"])
        self.assertEqual(set(models["DiscussionMessageInput"]["properties"]), {"text"})


class DiscussionStreamCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_closing_response_iterator_closes_service_generator(self):
        closed = False

        async def stream():
            nonlocal closed
            try:
                yield DiscussionAcceptedEvent(message=message())
                yield DiscussionDeltaEvent(text="Pending")
            finally:
                closed = True

        events = _reply_events(stream())
        await anext(events)
        await events.aclose()
        self.assertTrue(closed)

    async def test_cancelled_stream_propagates_and_closes_service_generator(self):
        started = asyncio.Event()
        closed = False

        async def stream():
            nonlocal closed
            try:
                yield DiscussionAcceptedEvent(message=message())
                started.set()
                await asyncio.Event().wait()
            finally:
                closed = True

        events = _reply_events(stream())
        await anext(events)
        task = asyncio.create_task(anext(events))
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(closed)
