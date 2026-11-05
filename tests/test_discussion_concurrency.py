"""Behavioral checks for one active turn per discussion, without model or database calls."""

import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from src.api import (
    DiscussionAcceptedEvent,
    DiscussionMessageResponse,
    DiscussionReplyResponse,
    DiscussionResponse,
    DiscussionService,
    DiscussionState,
    create_app,
)
from src.api.services.discussion import SerializedDiscussionService


def reply(text="Synthetic reply"):
    def message(role, content):
        return DiscussionMessageResponse(
            id=uuid4(),
            role=role,
            content=content,
            created_at=datetime(2026, 10, 24, tzinfo=timezone.utc),
        )

    return DiscussionReplyResponse(
        user_message=message("user", text), assistant_message=message("assistant", text)
    )


class DiscussionConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.match_id = uuid4()
        self.service = Mock(spec=DiscussionService)
        self.service.send_message.return_value = reply()
        self.service.get_discussion.return_value = DiscussionResponse(
            state=DiscussionState.OPEN, messages=[]
        )
        self.serialized = SerializedDiscussionService(self.service)

    async def waiting(self, operation):
        entered = asyncio.Event()

        async def run():
            entered.set()
            return await operation

        task = asyncio.create_task(run())
        await asyncio.wait_for(entered.wait(), timeout=2)
        return task

    async def test_second_turn_reads_the_first_completed_answer(self):
        entered, release = asyncio.Event(), asyncio.Event()
        history = []
        snapshots = []

        async def send(match_id, text):
            snapshots.append(list(history))
            history.append(("user", text))
            if text == "first":
                entered.set()
                await release.wait()
            history.append(("assistant", text))
            return reply(text)

        self.service.send_message.side_effect = send
        first = asyncio.create_task(self.serialized.send_message(self.match_id, "first"))
        await asyncio.wait_for(entered.wait(), timeout=2)
        second = await self.waiting(self.serialized.send_message(self.match_id, "second"))
        self.assertEqual(self.service.send_message.await_count, 1)
        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), timeout=1)
        self.assertEqual(snapshots, [[], [("user", "first"), ("assistant", "first")]])
        self.assertEqual(
            history,
            [
                ("user", "first"),
                ("assistant", "first"),
                ("user", "second"),
                ("assistant", "second"),
            ],
        )

    async def test_other_discussions_and_reads_do_not_wait(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def send(match_id, text):
            if match_id == self.match_id:
                entered.set()
                await release.wait()
            return reply(text)

        self.service.send_message.side_effect = send
        first = asyncio.create_task(self.serialized.send_message(self.match_id, "first"))
        await asyncio.wait_for(entered.wait(), timeout=2)
        await asyncio.wait_for(self.serialized.send_message(uuid4(), "other"), timeout=1)
        history = await asyncio.wait_for(self.serialized.get_discussion(self.match_id), timeout=1)
        self.assertEqual(history.state, DiscussionState.OPEN)
        release.set()
        await first

    async def test_start_send_and_finish_wait_for_stream_closure(self):
        for name in ["start", "send_message", "finish"]:
            with self.subTest(operation=name):

                async def stream():
                    yield DiscussionAcceptedEvent(message=reply().user_message)

                self.service.stream_message.side_effect = lambda *args: stream()
                events = await self.serialized.stream_message(self.match_id, "stream")
                await anext(events)
                operation = getattr(self.serialized, name)
                args = (self.match_id, "next") if name == "send_message" else (self.match_id,)
                before = getattr(self.service, name).await_count
                waiter = await self.waiting(operation(*args))
                self.assertEqual(getattr(self.service, name).await_count, before)
                await events.aclose()
                await asyncio.wait_for(waiter, timeout=1)
                self.assertEqual(getattr(self.service, name).await_count, before + 1)

    async def test_stream_preflight_waits_and_never_started_stream_can_close(self):
        async def stream():
            yield DiscussionAcceptedEvent(message=reply().user_message)

        self.service.stream_message.side_effect = lambda *args: stream()
        first = await self.serialized.stream_message(self.match_id, "first")
        second_task = await self.waiting(self.serialized.stream_message(self.match_id, "second"))
        self.assertEqual(self.service.stream_message.await_count, 1)
        await first.aclose()  # No iteration: still must release the preflight lock.
        await first.aclose()  # Explicit close is idempotent.
        second = await asyncio.wait_for(second_task, timeout=1)
        self.assertEqual(self.service.stream_message.await_count, 2)
        await second.aclose()

    async def test_preflight_failure_and_cancelled_preflight_release_lock(self):
        self.service.stream_message.side_effect = RuntimeError("Preflight failed")
        with self.assertRaises(RuntimeError):
            await self.serialized.stream_message(self.match_id, "first")
        await asyncio.wait_for(self.serialized.finish(self.match_id), timeout=1)

        entered = asyncio.Event()

        async def preflight(*args):
            entered.set()
            await asyncio.Event().wait()

        self.service.stream_message.side_effect = preflight
        task = asyncio.create_task(self.serialized.stream_message(self.match_id, "first"))
        await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(self.serialized.finish(self.match_id), timeout=1)

    async def test_regular_failure_and_cancellation_release_lock(self):
        self.service.send_message.side_effect = RuntimeError("Reply failed")
        with self.assertRaises(RuntimeError):
            await self.serialized.send_message(self.match_id, "first")
        await asyncio.wait_for(self.serialized.finish(self.match_id), timeout=1)

        entered = asyncio.Event()

        async def send(*args):
            entered.set()
            await asyncio.Event().wait()

        self.service.send_message.side_effect = send
        task = asyncio.create_task(self.serialized.send_message(self.match_id, "first"))
        await asyncio.wait_for(entered.wait(), timeout=2)
        finish = await self.waiting(self.serialized.finish(self.match_id))
        self.assertFalse(finish.done())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(finish, timeout=1)

    async def test_stream_failure_and_exhaustion_release_lock(self):
        async def failed():
            yield DiscussionAcceptedEvent(message=reply().user_message)
            raise RuntimeError("Reply failed")

        self.service.stream_message.side_effect = lambda *args: failed()
        events = await self.serialized.stream_message(self.match_id, "first")
        await anext(events)
        with self.assertRaises(RuntimeError):
            await anext(events)
        await asyncio.wait_for(self.serialized.finish(self.match_id), timeout=1)

        async def completed():
            yield DiscussionAcceptedEvent(message=reply().user_message)

        self.service.stream_message.side_effect = lambda *args: completed()
        events = await self.serialized.stream_message(self.match_id, "next")
        self.assertEqual(len([event async for event in events]), 1)
        await asyncio.wait_for(self.serialized.finish(self.match_id), timeout=1)

    async def test_stream_cancellation_closes_source_and_unblocks_finish(self):
        entered, closed = asyncio.Event(), asyncio.Event()

        async def stream():
            try:
                yield DiscussionAcceptedEvent(message=reply().user_message)
                entered.set()
                await asyncio.Event().wait()
            finally:
                closed.set()

        self.service.stream_message.side_effect = lambda *args: stream()
        events = await self.serialized.stream_message(self.match_id, "first")
        await anext(events)
        consume = asyncio.create_task(anext(events))
        await asyncio.wait_for(entered.wait(), timeout=2)
        finish = await self.waiting(self.serialized.finish(self.match_id))
        self.assertFalse(finish.done())
        consume.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await consume
        await asyncio.wait_for(finish, timeout=1)
        self.assertTrue(closed.is_set())

    async def test_cancelling_a_waiter_does_not_release_another_turn(self):
        async def stream():
            yield DiscussionAcceptedEvent(message=reply().user_message)

        self.service.stream_message.side_effect = lambda *args: stream()
        active = await self.serialized.stream_message(self.match_id, "active")
        cancelled = await self.waiting(self.serialized.finish(self.match_id))
        cancelled.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await cancelled
        waiting = await self.waiting(self.serialized.send_message(self.match_id, "next"))
        self.service.send_message.assert_not_awaited()
        await active.aclose()
        await asyncio.wait_for(waiting, timeout=1)

    async def test_app_reuses_the_coordinator_for_overlapping_requests(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def send(match_id, text):
            if text == "first":
                entered.set()
                await release.wait()
            return reply(text)

        self.service.send_message.side_effect = send
        app = create_app(discussion_service=self.service)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            url = f"/api/matches/{self.match_id}/discussion/messages"
            first = asyncio.create_task(client.post(url, json={"text": "first"}))
            await asyncio.wait_for(entered.wait(), timeout=2)
            second = await self.waiting(client.post(url, json={"text": "second"}))
            self.assertEqual(self.service.send_message.await_count, 1)
            release.set()
            responses = await asyncio.wait_for(asyncio.gather(first, second), timeout=2)
            self.assertEqual([response.status_code for response in responses], [200, 200])
