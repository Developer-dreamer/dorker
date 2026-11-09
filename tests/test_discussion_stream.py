import asyncio
import unittest
from contextlib import aclosing
from pathlib import Path
from types import SimpleNamespace

from test_discussion import MemoryRepository, job, profile_classifier

from src.analytics.discussion import DiscussionService, ReplyEvent
from src.analytics.generation.discussion_openai import OpenAIDiscussionModel
from src.analytics.profile_repository_xml import ProfileRepositoryXML


class DiscussionStreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repository = MemoryRepository()
        self.model = SimpleNamespace()
        self.service = DiscussionService(
            self.repository,
            self.model,
            ProfileRepositoryXML(Path(__file__).parent / "fixtures/profile.xml"),
            profile_classifier(),
        )
        self.job = job()
        self.match_id = self.job.match.id
        await self.service.start(self.job, 1, 1)

    async def test_deltas_arrive_before_completed_reply_is_saved(self):
        metadata = {"output": [{"type": "reasoning", "encrypted_content": "synthetic"}]}

        async def stream(_):
            yield ReplyEvent(delta="Hello ")
            yield ReplyEvent(delta="world")
            yield ReplyEvent(answer="Hello world", metadata=metadata)

        self.model.stream_reply = stream
        async with aclosing(self.service.stream_send(self.match_id, 1, 1, "Question")) as events:
            self.assertEqual((await anext(events))["type"], "accepted")
            self.assertEqual(self.repository.messages[-1].content, "Question")
            self.assertEqual((await anext(events)), {"type": "delta", "text": "Hello "})
            self.assertEqual(len(self.repository.messages), 2)
            self.assertEqual((await anext(events))["text"], "world")
            self.assertEqual((await anext(events))["type"], "done")
        self.assertEqual(self.repository.messages[-1].metadata, metadata)
        self.assertEqual(self.repository.messages[-1].content, "Hello world")

    async def test_failure_keeps_user_but_not_partial_reply(self):
        async def stream(_):
            yield ReplyEvent(delta="Partial")
            raise RuntimeError("provider failed")

        self.model.stream_reply = stream
        with self.assertRaisesRegex(RuntimeError, "provider failed"):
            async for _ in self.service.stream_send(self.match_id, 1, 1, "Question"):
                pass
        self.assertEqual([m.role for m in self.repository.messages], ["user", "user"])

    async def test_cancellation_closes_provider_and_releases_lock(self):
        closed = asyncio.Event()

        async def stream(_):
            try:
                yield ReplyEvent(delta="Partial")
                await asyncio.Event().wait()
            finally:
                closed.set()

        self.model.stream_reply = stream
        events = self.service.stream_send(self.match_id, 1, 1, "Question")
        await anext(events)
        await anext(events)
        await events.aclose()
        self.assertTrue(closed.is_set())
        self.assertFalse(self.service._lock(self.match_id, 1, 1).locked())
        self.assertEqual(len(self.repository.messages), 2)

    async def test_finished_discussion_rejects_send_without_saving(self):
        await self.service.finish(self.match_id, 1, 1)
        count = len(self.repository.messages)
        with self.assertRaisesRegex(ValueError, "finished"):
            await anext(self.service.stream_send(self.match_id, 1, 1, "Question"))
        self.assertEqual(len(self.repository.messages), count)

    async def test_missing_completion_is_not_saved_as_success(self):
        async def stream(_):
            yield ReplyEvent(delta="Partial")

        self.model.stream_reply = stream
        with self.assertRaisesRegex(ValueError, "did not complete"):
            async for _ in self.service.stream_send(self.match_id, 1, 1, "Question"):
                pass
        self.assertEqual(len(self.repository.messages), 2)


class ProviderStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_streams_text_and_preserves_final_metadata(self):
        output = {"type": "message", "content": [{"type": "output_text", "text": "Hello"}]}
        response = SimpleNamespace(
            status="completed",
            id="resp_test",
            model="configured",
            usage=None,
            output_text="Hello",
            output=[SimpleNamespace(model_dump=lambda **_: output)],
        )

        class Stream:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                self.closed = True

            def __aiter__(self):
                return self.events()

            async def events(self):
                yield SimpleNamespace(type="response.output_text.delta", delta="Hel")
                yield SimpleNamespace(type="response.output_text.delta", delta="lo")

            async def get_final_response(self):
                return response

        stream = Stream()
        calls = []

        def create_stream(**kwargs):
            calls.append(kwargs)
            return stream

        provider = OpenAIDiscussionModel(
            "configured", client=SimpleNamespace(responses=SimpleNamespace(stream=create_stream))
        )
        events = [
            event async for event in provider.stream_reply([{"role": "user", "content": "Hi"}])
        ]
        self.assertEqual([event.delta for event in events[:-1]], ["Hel", "lo"])
        self.assertEqual(events[-1].answer, "Hello")
        self.assertEqual(events[-1].metadata["output"], [output])
        self.assertEqual(calls[0]["include"], ["reasoning.encrypted_content"])
        self.assertFalse(calls[0]["store"])
        self.assertTrue(stream.closed)
