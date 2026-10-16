"""Local single-user HTTP adapter; business rules remain in analytics services."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing, asynccontextmanager
from typing import Annotated, Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from src.analytics.discussion import DiscussionService
from src.analytics.protocols import MatchingProvider
from src.shared.models import (
    ApplicationGeneratedResponse,
    ApplicationPacket,
    JobForAnalytics,
    Material,
    RequestedGeneration,
    SuitabilityTier,
)
from src.shared.models.discussion import DiscussionMessage

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=16000)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TextInput(Input):
    text: Text


class StatusInput(Input):
    status: Literal["PENDING", "SKIPPED", "IGNORED", "APPLIED", "DECLINED"]


class GenerationInput(Input):
    cover_letter: bool = True
    questions: bool = True

    @model_validator(mode="after")
    def require_output(self) -> "GenerationInput":
        if not self.cover_letter and not self.questions:
            raise ValueError("Select cover_letter, questions, or both.")
        return self


class MessageInput(TextInput):
    confirmed: bool = False


class MaterialInput(Input):
    material_path: Text


class DiscussionView(BaseModel):
    messages: list[DiscussionMessage]
    selected_packet: ApplicationPacket | None
    confirmed_materials: list[Material]


def create_app(
    engine: MatchingProvider,
    discussion: DiscussionService,
    *,
    user_id: int,
    chat_id: int,
    allowed_origins: list[str] | None = None,
) -> FastAPI:
    """Identity is configured by the server, never supplied by HTTP clients."""
    app = FastAPI(title="Dorker API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins or ["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Content-Type"],
    )
    locks: dict[UUID, asyncio.Lock] = {}

    @asynccontextmanager
    async def mutation(match_id: UUID) -> AsyncIterator[JobForAnalytics]:
        # Serialize API mutations per application, not per browser/chat.
        async with locks.setdefault(match_id, asyncio.Lock()):
            yield await get_job(match_id)

    async def get_job(match_id: UUID) -> JobForAnalytics:
        job = await engine.get_job_by_match_id(match_id)
        if job is None:
            raise HTTPException(404, "Match not found.")
        return job

    @app.exception_handler(ValueError)
    async def invalid_action(request: Request, error: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(error)})

    @app.exception_handler(Exception)
    async def failed_action(request: Request, error: Exception) -> JSONResponse:
        logging.getLogger(__name__).error("API request failed", exc_info=error)
        return JSONResponse(
            status_code=500, content={"detail": "Request failed. Check server logs."}
        )

    @app.get("/api/matches/counts")
    async def counts() -> dict[str, int]:
        return await engine.get_matching_count()

    @app.get("/api/matches/next")
    async def next_match(
        offset: Annotated[int, Query(ge=0)] = 0,
        tiers: Annotated[list[SuitabilityTier] | None, Query()] = None,
    ) -> JobForAnalytics | None:
        return await engine.get_matched_job(
            tiers or [SuitabilityTier.SUITABLE, SuitabilityTier.RUNWAY, SuitabilityTier.STRETCH],
            offset,
        )

    @app.get("/api/matches/{match_id}")
    async def match(match_id: UUID) -> JobForAnalytics:
        return await get_job(match_id)

    @app.get("/api/matches/{match_id}/debug")
    async def debug(match_id: UUID) -> dict[str, str | None]:
        try:
            return {"debug": await engine.get_match_debug(match_id)}
        except ValueError as error:
            raise HTTPException(404, str(error)) from error

    @app.patch("/api/matches/{match_id}/status")
    async def status(match_id: UUID, body: StatusInput) -> StatusInput:
        async with mutation(match_id):
            await engine.update_pipeline_status(match_id, body.status)
            await discussion.record_action(match_id, user_id, chat_id, f"status:{body.status}")
        return body

    @app.post("/api/matches/{match_id}/reports", status_code=204)
    async def report(match_id: UUID, body: TextInput) -> None:
        async with mutation(match_id):
            await engine.report_match(match_id, body.text)

    @app.post("/api/matches/{match_id}/questions", status_code=204)
    async def question(match_id: UUID, body: TextInput) -> None:
        async with mutation(match_id):
            await engine.append_question(match_id, body.text)

    @app.post("/api/matches/{match_id}/generation")
    async def generate(match_id: UUID, body: GenerationInput) -> ApplicationGeneratedResponse:
        async with mutation(match_id) as job:
            materials = await discussion.confirmed_materials(match_id, user_id, chat_id)
            packet = await discussion.selected_packet(match_id, user_id, chat_id)
            result = await engine.generate_application_for_match(
                RequestedGeneration(**body.model_dump()),
                job,
                materials=materials or None,
                selected_packet=packet,
            )
            await discussion.record_action(match_id, user_id, chat_id, "generation")
            return result

    @app.get("/api/materials")
    async def materials(q: str = "") -> list[Material]:
        return [m for m in discussion.profile.get_materials() if q.casefold() in m.id.casefold()]

    @app.get("/api/matches/{match_id}/discussion")
    async def history(match_id: UUID) -> DiscussionView:
        await get_job(match_id)
        return DiscussionView(
            messages=await discussion.repository.history(match_id, user_id, chat_id),
            selected_packet=await discussion.selected_packet(match_id, user_id, chat_id),
            confirmed_materials=await discussion.confirmed_materials(match_id, user_id, chat_id),
        )

    @app.post("/api/matches/{match_id}/discussion/start", status_code=204)
    async def start(match_id: UUID) -> None:
        async with mutation(match_id) as job:
            await discussion.start(job, user_id, chat_id)

    @app.post("/api/matches/{match_id}/discussion/messages")
    async def send(match_id: UUID, body: MessageInput) -> dict[str, str]:
        async with mutation(match_id):
            answer = await discussion.send(
                match_id,
                user_id,
                chat_id,
                body.text,
                confirmed=body.confirmed,
            )
            return {"answer": answer}

    @app.post("/api/matches/{match_id}/discussion/stream")
    async def stream_message(match_id: UUID, body: TextInput) -> StreamingResponse:
        await get_job(match_id)

        async def events() -> AsyncIterator[str]:
            saved = False
            try:
                yield json.dumps({"type": "thinking"}) + "\n"
                async with mutation(match_id) as job:
                    if not await discussion.repository.history(match_id, user_id, chat_id):
                        await discussion.start(job, user_id, chat_id)
                    packet = await discussion.selected_packet(match_id, user_id, chat_id)
                    confirmed = await discussion.confirmed_materials(match_id, user_id, chat_id)
                    material_ids = list(
                        dict.fromkeys(
                            material.id
                            for material in [*(packet.materials if packet else []), *confirmed]
                        )
                    )
                    yield json.dumps({"type": "evidence", "ids": material_ids}) + "\n"
                    async with aclosing(
                        discussion.stream_send(match_id, user_id, chat_id, body.text)
                    ) as stream:
                        async for event in stream:
                            if event["type"] == "accepted":
                                saved = True
                            yield json.dumps(event) + "\n"
            except ValueError as error:
                yield json.dumps({"type": "error", "detail": str(error), "saved": saved}) + "\n"
            except Exception:
                logging.getLogger(__name__).exception("Discussion stream failed")
                yield (
                    json.dumps(
                        {
                            "type": "error",
                            "detail": "Reply failed. Please reload the discussion before retrying.",
                            "saved": saved,
                        }
                    )
                    + "\n"
                )

        return StreamingResponse(
            events(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/matches/{match_id}/discussion/materials")
    async def select_material(match_id: UUID, body: MaterialInput) -> dict[str, str]:
        async with mutation(match_id):
            material = discussion.profile.get_material(body.material_path)
            answer = await discussion.send(
                match_id,
                user_id,
                chat_id,
                material.text,
                confirmed=True,
                material_path=material.id,
            )
            return {"answer": answer}

    @app.post("/api/matches/{match_id}/discussion/finish", status_code=204)
    async def finish(match_id: UUID) -> None:
        async with mutation(match_id):
            await discussion.finish(match_id, user_id, chat_id)

    return app
