import logging
from collections.abc import AsyncGenerator, Iterator
from contextlib import aclosing, contextmanager
from uuid import UUID

from litestar import Controller, get, post
from litestar.background_tasks import BackgroundTask
from litestar.di import NamedDependency
from litestar.exceptions import HTTPException
from litestar.openapi.datastructures import ResponseSpec
from litestar.params import FromPath, JSONBody, SkipValidation
from litestar.response import Stream

from src.api.dependencies import require_service
from src.api.exceptions import (
    DiscussionConflictException,
    DiscussionReplyException,
    NotFoundException,
)
from src.api.models import (
    DiscussionErrorEvent,
    DiscussionMessageInput,
    DiscussionReplyEvent,
    DiscussionReplyResponse,
    DiscussionResponse,
)
from src.api.services import DiscussionService

logger = logging.getLogger(__name__)


async def _reply_events(
    stream: AsyncGenerator[DiscussionReplyEvent, None],
) -> AsyncGenerator[str, None]:
    saved = False
    try:
        # Cancellation must propagate; closing the generator releases service resources.
        async with aclosing(stream):
            async for event in stream:
                if event.type == "accepted":
                    saved = True
                yield event.model_dump_json() + "\n"
    except DiscussionReplyException as error:
        yield (
            DiscussionErrorEvent(detail=str(error), saved=saved or error.saved).model_dump_json()
            + "\n"
        )
    except DiscussionConflictException as error:
        yield DiscussionErrorEvent(detail=str(error), saved=saved).model_dump_json() + "\n"
    except NotFoundException:
        yield (
            DiscussionErrorEvent(detail="Match not found.", saved=saved).model_dump_json() + "\n"
        )
    except Exception:
        logger.exception("Discussion stream failed")
        yield (
            DiscussionErrorEvent(
                detail="Reply failed. Reload the discussion before retrying.", saved=saved
            ).model_dump_json()
            + "\n"
        )


class DiscussionHandler(Controller):
    path = "/api/matches/{id:str}/discussion"
    tags = ["Discussion"]
    responses = {
        422: ResponseSpec(None, description="Invalid request parameters or body."),
        503: ResponseSpec(None, description="The requested service is not configured."),
        404: ResponseSpec(None, description="Match not found."),
        409: ResponseSpec(None, description="Discussion lifecycle conflict."),
        502: ResponseSpec(None, description="Reply failed; detail and saved describe recovery."),
    }

    @contextmanager
    def _match_errors(self) -> Iterator[None]:
        """Translate missing matches; typed discussion errors use the shared handlers."""
        try:
            yield
        except NotFoundException as error:
            raise HTTPException(status_code=404, detail="Match not found.") from error
        except ValueError as error:
            # Service validation failures are unexpected, unlike HTTP request validation.
            logger.exception("Discussion request failed")
            raise HTTPException(
                status_code=500, detail="Request failed. Check server logs."
            ) from error

    @get(responses=responses)
    async def get_discussion(
        self,
        id: FromPath[UUID],
        discussion_service: NamedDependency[SkipValidation[DiscussionService | None]],
    ) -> DiscussionResponse:
        discussion_service = require_service(discussion_service, "Discussion")
        with self._match_errors():
            return await discussion_service.get_discussion(id)

    @post("/start", status_code=204, responses=responses)
    async def start(
        self,
        id: FromPath[UUID],
        discussion_service: NamedDependency[SkipValidation[DiscussionService | None]],
    ) -> None:
        discussion_service = require_service(discussion_service, "Discussion")
        with self._match_errors():
            await discussion_service.start(id)

    @post("/messages", status_code=200, responses=responses)
    async def send_message(
        self,
        id: FromPath[UUID],
        data: JSONBody[DiscussionMessageInput],
        discussion_service: NamedDependency[SkipValidation[DiscussionService | None]],
    ) -> DiscussionReplyResponse:
        discussion_service = require_service(discussion_service, "Discussion")
        with self._match_errors():
            return await discussion_service.send_message(id, data.text)

    @post("/stream", status_code=200, media_type="application/x-ndjson", responses=responses)
    async def stream_message(
        self,
        id: FromPath[UUID],
        data: JSONBody[DiscussionMessageInput],
        discussion_service: NamedDependency[SkipValidation[DiscussionService | None]],
    ) -> Stream:
        discussion_service = require_service(discussion_service, "Discussion")
        # Await preflight while missing-match and lifecycle errors can still use HTTP statuses.
        with self._match_errors():
            stream = await discussion_service.stream_message(id, data.text)
        events = _reply_events(stream)

        async def close_events() -> None:
            # Wrap aclose in an async function: Litestar otherwise treats the built-in method as sync.
            try:
                await events.aclose()
            finally:
                # Closing an unstarted async generator skips its finally block.
                await stream.aclose()

        return Stream(
            events,
            # A disconnect may cancel a network write while the generator is suspended at yield.
            # Close it explicitly after Litestar has stopped its streaming task.
            background=BackgroundTask(close_events),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
