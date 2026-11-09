"""Per-discussion coordination for an injected service in a single application process."""

import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

from src.api.models import DiscussionReplyEvent, DiscussionReplyResponse, DiscussionResponse
from src.api.services.protocols import DiscussionService


class _LockedReplyStream(AsyncGenerator[DiscussionReplyEvent, None]):
    """Own the turn lock until exhaustion, failure, cancellation, or explicit close.

    A plain generator's finally block does not run when it is closed before its first
    iteration. This adapter must also release a lock acquired during HTTP preflight
    when the caller closes a stream it never started consuming.
    """

    def __init__(
        self, stream: AsyncGenerator[DiscussionReplyEvent, None], lock: asyncio.Lock
    ) -> None:
        self._stream = stream
        self._lock = lock
        self._closed = False

    async def __anext__(self) -> DiscussionReplyEvent:
        return await self.asend(None)

    async def asend(self, value: None) -> DiscussionReplyEvent:
        if self._closed:
            raise StopAsyncIteration
        try:
            return await self._stream.asend(value)
        except BaseException:
            # CancelledError also ends a turn; cancellation must propagate after cleanup.
            await self.aclose()
            raise

    async def athrow(self, *args: Any) -> DiscussionReplyEvent:
        if self._closed:
            raise StopAsyncIteration
        try:
            return await self._stream.athrow(*args)
        except BaseException:
            await self.aclose()
            raise

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                await self._stream.aclose()
            finally:
                self._lock.release()


class SerializedDiscussionService:
    """Wait for the previous mutation of this discussion, then read its updated state.

    Construct once and reuse across requests. The wrapped service is already scoped
    to a user/chat, so match_id identifies its discussion. This coordinates one process;
    it does not replace database coordination between workers or clients bypassing it.
    """

    def __init__(self, service: DiscussionService) -> None:
        self._service = service
        self._locks: dict[UUID, asyncio.Lock] = {}

    def _lock(self, match_id: UUID) -> asyncio.Lock:
        return self._locks.setdefault(match_id, asyncio.Lock())

    async def get_discussion(self, match_id: UUID) -> DiscussionResponse:
        # Readers may see a persisted user message while its assistant reply is pending.
        return await self._service.get_discussion(match_id)

    async def start(self, match_id: UUID) -> None:
        async with self._lock(match_id):
            await self._service.start(match_id)

    async def send_message(self, match_id: UUID, text: str) -> DiscussionReplyResponse:
        async with self._lock(match_id):
            return await self._service.send_message(match_id, text)

    async def stream_message(
        self, match_id: UUID, text: str
    ) -> AsyncGenerator[DiscussionReplyEvent, None]:
        lock = self._lock(match_id)
        await lock.acquire()
        try:
            # Preflight observes state after previous turns finish, before HTTP headers are sent.
            stream = await self._service.stream_message(match_id, text)
        except BaseException:
            lock.release()
            raise
        # Ownership transfers to the stream, not to the HTTP handler or its constructor.
        return _LockedReplyStream(stream, lock)
