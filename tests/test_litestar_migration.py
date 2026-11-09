"""Framework-boundary checks: DI isolation, docs, and real ASGI disconnect cleanup."""

import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from litestar.testing import TestClient

from src.api import (
    DiscussionAcceptedEvent,
    DiscussionMessageResponse,
    DiscussionService,
    JobService,
    JobsResponse,
    create_app,
)


class LitestarMigrationTests(unittest.TestCase):
    def test_docs_and_service_bindings_are_independent_between_apps(self):
        first, second = Mock(spec=JobService), Mock(spec=JobService)
        first.list_jobs.return_value = JobsResponse(jobs=[], total=1, limit=50, offset=0)
        second.list_jobs.return_value = JobsResponse(jobs=[], total=2, limit=50, offset=0)
        with TestClient(create_app(first)) as a, TestClient(create_app(second)) as b:
            self.assertEqual(a.get("/api/jobs").json()["total"], 1)
            self.assertEqual(b.get("/api/jobs").json()["total"], 2)
            self.assertEqual(a.get("/api/jobs").json()["total"], 1)
            self.assertEqual(a.get("/docs").status_code, 200)
            self.assertIn("swagger", a.get("/docs").text.lower())
            schema = a.get("/openapi.json").json()
            self.assertEqual(schema["info"]["title"], "Dorker API")
            params = schema["paths"]["/api/jobs"]["get"]["parameters"]
            self.assertEqual({p["name"] for p in params}, {"limit", "offset"})

    def test_validation_precedes_unconfigured_service_errors_for_every_domain(self):
        with TestClient(create_app()) as client:
            requests = [
                ("GET", "/api/jobs?limit=251", None),
                ("GET", "/api/matches/invalid", None),
                ("POST", f"/api/matches/{uuid4()}/discussion/messages", {"text": " "}),
                ("PATCH", "/api/applications/0", {"cover_letter": "Hi"}),
            ]
            for method, url, body in requests:
                with self.subTest(url=url):
                    response = client.request(method, url, json=body)
                    self.assertEqual(response.status_code, 422)
                    self.assertIn("detail", response.json())

    def test_malformed_json_and_cors_on_error_responses(self):
        with TestClient(create_app()) as client:
            response = client.post(
                "/api/applications/1/generation",
                content="{",
                headers={"content-type": "application/json", "origin": "http://localhost:5173"},
            )
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json(), {"detail": "Malformed request body."})
            self.assertEqual(
                response.headers["access-control-allow-origin"], "http://localhost:5173"
            )
            denied = client.get("/api/jobs", headers={"origin": "https://untrusted.example"})
            self.assertNotIn("access-control-allow-origin", denied.headers)


class LitestarDisconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_closes_service_while_waiting_for_model_or_network_write(self):
        for blocked_send in [False, True, "before_stream"]:
            with self.subTest(blocked_send=blocked_send):
                disconnected = asyncio.Event()
                closed = False
                stream_started = False
                first = True

                async def stream():
                    nonlocal closed, stream_started
                    stream_started = True
                    try:
                        yield DiscussionAcceptedEvent(
                            message=DiscussionMessageResponse(
                                id=uuid4(),
                                role="user",
                                content="Hi",
                                created_at=datetime.now(timezone.utc),
                            )
                        )
                        await asyncio.Event().wait()
                    finally:
                        closed = True

                service = Mock(spec=DiscussionService)
                service.stream_message.return_value = stream()
                app = create_app(discussion_service=service)
                path = f"/api/matches/{uuid4()}/discussion/stream"
                received = []

                async def receive(disconnected=disconnected, blocked_send=blocked_send):
                    nonlocal first
                    if first:
                        first = False
                        return {
                            "type": "http.request",
                            "body": b'{"text":"Hi"}',
                            "more_body": False,
                        }
                    if blocked_send != "before_stream":
                        await disconnected.wait()
                    return {"type": "http.disconnect"}

                async def send(
                    event, disconnected=disconnected, received=received, blocked_send=blocked_send
                ):
                    received.append(event)
                    if event["type"] == "http.response.body" and event.get("body"):
                        disconnected.set()
                        if blocked_send is True:
                            # Cancellation here suspends the iterator at its last yield.
                            await asyncio.Event().wait()

                scope = {
                    "type": "http",
                    "asgi": {"version": "3.0", "spec_version": "2.3"},
                    "http_version": "1.1",
                    "method": "POST",
                    "scheme": "http",
                    "path": path,
                    "raw_path": path.encode(),
                    "query_string": b"",
                    "headers": [(b"content-type", b"application/json")],
                    "client": ("127.0.0.1", 1),
                    "server": ("localhost", 8000),
                    "root_path": "",
                }
                await asyncio.wait_for(app(scope, receive, send), timeout=2)
                self.assertTrue(closed or not stream_started)
                self.assertEqual(received[0]["status"], 200)
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://testserver"
                ) as client:
                    response = await asyncio.wait_for(
                        client.post(path.removesuffix("/stream") + "/finish"), timeout=1
                    )
                    self.assertEqual(response.status_code, 204)
                    service.finish.assert_awaited_once()
