"""Scope the analytics conversation service to the API's configured local identity."""

import logging
from collections.abc import AsyncGenerator
from contextlib import aclosing
from typing import Any
from uuid import UUID

import httpx
from openai import OpenAIError
from pydantic import ValidationError

from src.analytics.discussion import (
    DiscussionModel,
    DiscussionRepository,
    ReplyEvent,
)
from src.analytics.discussion import (
    DiscussionService as AnalyticsDiscussionService,
)
from src.api.exceptions import (
    DiscussionConflictException,
    DiscussionReplyException,
    NotFoundException,
)
from src.api.models import (
    DiscussionAcceptedEvent,
    DiscussionDeltaEvent,
    DiscussionDoneEvent,
    DiscussionMessageResponse,
    DiscussionReplyEvent,
    DiscussionReplyResponse,
    DiscussionResponse,
    DiscussionState,
)
from src.api.services.protocols import DiscussionService as DiscussionServiceProtocol
from src.database.protocols import MatchRepository
from src.shared.models.discussion import DiscussionMessage


def visible(message: DiscussionMessage) -> DiscussionMessageResponse:
    return DiscussionMessageResponse(
        id=message.id, role=message.role, content=message.content, created_at=message.created_at
    )


class SafeDiscussionModel:
    """Called only after the user message is saved; provider failures therefore have saved=True."""

    def __init__(self, model: DiscussionModel) -> None:
        self.model = model

    async def reply(self, messages: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
        try:
            answer, metadata = await self.model.reply(messages)
            if not answer.strip():
                raise DiscussionReplyException(saved=True)
            return answer, metadata
        except (
            OpenAIError,
            httpx.HTTPError,
            ValidationError,
            ValueError,
            DiscussionReplyException,
        ) as error:
            logging.getLogger(__name__).exception("Discussion provider failed")
            raise DiscussionReplyException(saved=True) from error

    async def stream_reply(
        self, messages: list[dict[str, Any]]
    ) -> AsyncGenerator[ReplyEvent, None]:
        try:
            completed = False
            async with aclosing(self.model.stream_reply(messages)) as events:
                async for event in events:
                    if event.answer is not None:
                        completed = bool(event.answer.strip())
                    yield event
            if not completed:
                raise DiscussionReplyException(saved=True)
        except (
            OpenAIError,
            httpx.HTTPError,
            ValidationError,
            ValueError,
            DiscussionReplyException,
        ) as error:
            logging.getLogger(__name__).exception("Discussion provider stream failed")
            raise DiscussionReplyException(saved=True) from error


class DiscussionService(DiscussionServiceProtocol):
    """Visible history excludes context/action records, but retains persisted failed user turns.

    The app's SerializedDiscussionService coordinates preflight and mutations. This adapter
    uses one server-selected user/chat identity; clients cannot select another history.
    """

    def __init__(
        self,
        service: AnalyticsDiscussionService,
        repository: DiscussionRepository,
        matches: MatchRepository,
        *,
        user_id: int,
        chat_id: int,
    ) -> None:
        self.service = service
        self.repository = repository
        self.matches = matches
        self.user_id = user_id
        self.chat_id = chat_id

    async def _history(self, match_id: UUID) -> list[DiscussionMessage]:
        job = await self.matches.get_job_by_match_id(match_id)
        if job is None:
            raise NotFoundException("Match not found.")
        return await self.repository.history(match_id, self.user_id, self.chat_id)

    @staticmethod
    def _state(history: list[DiscussionMessage]) -> DiscussionState:
        if not history:
            return DiscussionState.NOT_STARTED
        if any(m.metadata.get("action") == "finish" for m in history):
            return DiscussionState.FINISHED
        return DiscussionState.OPEN

    async def _open(self, match_id: UUID) -> list[DiscussionMessage]:
        history = await self._history(match_id)
        if self._state(history) != DiscussionState.OPEN:
            raise DiscussionConflictException("Discussion must be open for this operation.")
        return history

    async def get_discussion(self, match_id: UUID) -> DiscussionResponse:
        history = await self._history(match_id)
        return DiscussionResponse(
            state=self._state(history),
            messages=[
                visible(m) for m in history if m.metadata.get("kind") not in ("context", "action")
            ],
        )

    async def start(self, match_id: UUID) -> None:
        if await self._history(match_id):
            raise DiscussionConflictException("Discussion has already started and cannot reopen.")
        job = await self.matches.get_job_by_match_id(match_id)
        if job is None:
            raise NotFoundException("Match not found.")
        await self.service.start(job, self.user_id, self.chat_id)

    async def send_message(self, match_id: UUID, text: str) -> DiscussionReplyResponse:
        await self._open(match_id)
        await self.service.send(match_id, self.user_id, self.chat_id, text)
        history = await self.repository.history(match_id, self.user_id, self.chat_id)
        return DiscussionReplyResponse(
            user_message=visible(history[-2]), assistant_message=visible(history[-1])
        )

    async def stream_message(
        self, match_id: UUID, text: str
    ) -> AsyncGenerator[DiscussionReplyEvent, None]:
        # Awaited preflight happens before HTTP headers; iteration starts persistence/provider I/O.
        await self._open(match_id)
        return self._stream(match_id, text)

    async def _stream(
        self, match_id: UUID, text: str
    ) -> AsyncGenerator[DiscussionReplyEvent, None]:
        async with aclosing(
            self.service.stream_send(match_id, self.user_id, self.chat_id, text)
        ) as events:
            async for event in events:
                if event["type"] == "delta":
                    yield DiscussionDeltaEvent(text=event["text"])
                else:
                    message = visible(event["message"])
                    if event["type"] == "accepted":
                        yield DiscussionAcceptedEvent(message=message)
                    else:
                        yield DiscussionDoneEvent(message=message)

    async def finish(self, match_id: UUID) -> None:
        await self._open(match_id)
        await self.service.finish(match_id, self.user_id, self.chat_id)
