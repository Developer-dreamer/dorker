import asyncio
import json
from typing import Any, Protocol
from uuid import UUID

from src.analytics.protocols import Classifier, ProfileRepository
from src.shared.models import ApplicationPacket, JobForAnalytics, Material
from src.shared.models.discussion import DiscussionMessage


class DiscussionRepository(Protocol):
    async def save(self, message: DiscussionMessage) -> None: ...
    async def history(
        self, match_id: UUID, user_id: int, chat_id: int
    ) -> list[DiscussionMessage]: ...


class DiscussionModel(Protocol):
    async def reply(self, messages: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]: ...


class DiscussionService:
    """
    Service which provides option to discuss an application.
    Exists because of uncertainty even after a Jev classification.
    The user can initiate a discussion, then relevant materials are loaded (profile overview, cv, etc.)
    and/or user can append his own. So that user can clarify further information about
    workplace, legal eligibility, technical requirements.

    Uses standard LLM's chatting architecture with sliding window of 15 messages default.
    """

    def __init__(
        self,
        repository: DiscussionRepository,
        model: DiscussionModel,
        profile: ProfileRepository,
        profile_classifier: Classifier[ApplicationPacket],
        history_messages: int = 15,
        history_chars: int = 24000,
    ) -> None:
        self.repository = repository
        self.model = model
        self.profile_classifier = profile_classifier
        self.profile = profile
        self.history_messages = history_messages
        self.history_chars = history_chars
        self._locks: dict[tuple[UUID, int, int], asyncio.Lock] = {}

    def _lock(self, match_id: UUID, user_id: int, chat_id: int) -> asyncio.Lock:
        return self._locks.setdefault((match_id, user_id, chat_id), asyncio.Lock())

    async def start(self, job: JobForAnalytics, user_id: int, chat_id: int) -> None:
        """
        Initiates discussion about the job application relevance.
        """
        if job.match is None:
            raise ValueError("Job has no match.")
        match_id = job.match.id
        async with self._lock(match_id, user_id, chat_id):
            if await self.repository.history(match_id, user_id, chat_id):
                raise ValueError("This match already has a discussion. Reopening is not supported.")
            packet = await self.profile_classifier.classify(job, self.profile)
            context = json.dumps(
                {
                    "candidate_cv": self.profile.get_cv().text,
                    "candidate_preferences": self.profile.get_preferences().text,
                    "selected_materials": [m.model_dump() for m in packet.materials],
                    "job": job.model_dump(
                        mode="json", exclude={"application", "match", "description_blocks"}
                    ),
                    "jev_assessment": job.match.model_dump(mode="json", exclude={"debug"}),
                },
                ensure_ascii=False,
            )
            await self.repository.save(
                DiscussionMessage(
                    match_id=match_id,
                    user_id=user_id,
                    chat_id=chat_id,
                    role="user",
                    content=context,
                    metadata={
                        "kind": "context",
                        "application_packet": packet.model_dump(mode="json"),
                        "match_debug": job.match.debug,
                    },
                )
            )

    @staticmethod
    def _require_open(history: list[DiscussionMessage]) -> None:
        if not history or history[0].metadata.get("kind") != "context":
            raise ValueError("No discussion started for this match.")
        if any(m.metadata.get("action") == "finish" for m in history):
            raise ValueError("Discussion has already finished.")

    def _input(self, history: list[DiscussionMessage]) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [{"role": "user", "content": history[0].content}]
        confirmed = [m for m in history if m.metadata.get("material")]
        if confirmed:
            messages.append(
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "user_confirmed_application_materials": [
                                m.metadata["material"] for m in confirmed
                            ],
                        },
                        ensure_ascii=False,
                    ),
                }
            )

        # Drop whole turns so a window never begins with an orphan assistant response.
        turns: list[list[DiscussionMessage]] = []
        for message in history[1:]:
            if message.metadata.get("kind") == "action":
                continue
            if message.role == "user":
                turns.append([message])
            elif turns:
                turns[-1].append(message)
        selected: list[list[DiscussionMessage]] = []
        count = size = 0
        for turn in reversed(turns):
            turn_size = sum(
                len(m.content) + len(json.dumps(m.metadata.get("output", []))) for m in turn
            )
            if selected and (
                count + len(turn) > self.history_messages or size + turn_size > self.history_chars
            ):
                break
            selected.insert(0, turn)
            count += len(turn)
            size += turn_size
        for turn in selected:
            for message in turn:
                output = message.metadata.get("output")
                if message.role == "assistant" and output:
                    messages.extend(output)
                else:
                    messages.append({"role": message.role, "content": message.content})
        return messages

    async def send(
        self,
        match_id: UUID,
        user_id: int,
        chat_id: int,
        text: str,
        *,
        confirmed: bool = False,
        material_path: str | None = None,
    ) -> str:
        text = text.strip()
        if not text or len(text) > 16000:
            raise ValueError("Send between 1 and 16,000 characters.")
        async with self._lock(match_id, user_id, chat_id):
            history = await self.repository.history(match_id, user_id, chat_id)
            self._require_open(history)
            message = DiscussionMessage(
                match_id=match_id,
                user_id=user_id,
                chat_id=chat_id,
                role="user",
                content=text,
            )
            if confirmed:
                material = Material(id=material_path or f"discussion.{message.id}", text=text)
                message.metadata["material"] = material.model_dump()
            await self.repository.save(message)
            history.append(message)
            answer, metadata = await self.model.reply(self._input(history))
            if not answer.strip():
                raise ValueError("The model returned no answer. Your message was saved; try again.")
            await self.repository.save(
                DiscussionMessage(
                    match_id=match_id,
                    user_id=user_id,
                    chat_id=chat_id,
                    role="assistant",
                    content=answer,
                    metadata=metadata,
                )
            )
            return answer

    async def finish(self, match_id: UUID, user_id: int, chat_id: int) -> None:
        async with self._lock(match_id, user_id, chat_id):
            history = await self.repository.history(match_id, user_id, chat_id)
            self._require_open(history)
            await self.repository.save(
                DiscussionMessage(
                    match_id=match_id,
                    user_id=user_id,
                    chat_id=chat_id,
                    role="user",
                    content="Finish discussion",
                    metadata={"kind": "action", "action": "finish"},
                )
            )

    async def confirmed_materials(
        self, match_id: UUID, user_id: int, chat_id: int
    ) -> list[Material]:
        history = await self.repository.history(match_id, user_id, chat_id)
        materials = [
            Material.model_validate(m.metadata["material"])
            for m in history
            if m.metadata.get("material")
        ]
        return list({m.id: m for m in materials}.values())

    async def selected_packet(
        self, match_id: UUID, user_id: int, chat_id: int
    ) -> ApplicationPacket | None:
        history = await self.repository.history(match_id, user_id, chat_id)
        if not history:
            return None
        packet = history[0].metadata.get("application_packet")
        if packet is None:
            # Older discussions have no saved selection; generation can classify normally.
            return None
        return ApplicationPacket.model_validate(packet)

    async def record_action(self, match_id: UUID, user_id: int, chat_id: int, action: str) -> None:
        if await self.repository.history(match_id, user_id, chat_id):
            await self.repository.save(
                DiscussionMessage(
                    match_id=match_id,
                    user_id=user_id,
                    chat_id=chat_id,
                    role="user",
                    content=action,
                    metadata={"kind": "action", "action": action},
                )
            )
