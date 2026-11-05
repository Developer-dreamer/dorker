"""Generate selected content, then save it through the existing packet repository."""

import asyncio
import logging
from uuid import UUID

import httpx
from openai import OpenAIError
from pydantic import ValidationError

from src.analytics.protocols import ApplicationGenerator, Classifier, ProfileRepository
from src.api.exceptions import ApplicationGenerationException, NotFoundException
from src.api.models import (
    ApplicationPatchInput,
    ApplicationQuestionResponse,
    ApplicationResponse,
    ApplicationsResponse,
    GenerationInput,
)
from src.api.services.protocols import ApplicationService as ApplicationServiceProtocol
from src.database.protocols import ApplicationPacketRepository, MatchRepository
from src.shared.models import (
    ApplicationGeneratedResponse,
    ApplicationPacket,
    JobForAnalytics,
    OpenAIBaseResponse,
    Question,
    RequestedCoverletter,
    RequestedGeneration,
    RequestedQuestions,
)


def application_response(packet: ApplicationPacket) -> ApplicationResponse:
    if packet.created_at is None:
        raise ValueError("Repository returned an unpersisted application.")
    return ApplicationResponse(
        id=packet.id,
        match_id=packet.match_id,
        job_id=packet.job_id,
        cover_letter=packet.cover_letter,
        questions=[
            ApplicationQuestionResponse(question=q.question, answer=q.answer)
            for q in packet.questions
        ],
        created_at=packet.created_at,
        updated_at=packet.updated_at,
    )


class ApplicationService(ApplicationServiceProtocol):
    """Reuse saved candidate materials and serialize API writes to each draft in this process.

    No database connection is held while waiting for a provider. A selected-section update is
    persisted only after generation completes; multiple workers would need shared coordination.
    """

    def __init__(
        self,
        repository: ApplicationPacketRepository,
        matches: MatchRepository,
        classifier: Classifier[ApplicationPacket],
        profile: ProfileRepository,
        generator: ApplicationGenerator[OpenAIBaseResponse],
    ) -> None:
        self.repository = repository
        self.matches = matches
        self.classifier = classifier
        self.profile = profile
        self.generator = generator
        self._locks: dict[int, asyncio.Lock] = {}

    async def _job(self, match_id: UUID) -> JobForAnalytics:
        job = await self.matches.get_job_by_match_id(match_id)
        if job is None:
            raise NotFoundException("Match not found.")
        return job.model_copy(deep=True)

    async def _packet(self, application_id: int) -> ApplicationPacket:
        packet = await self.repository.get_packet_by_id(application_id)
        if packet is None:
            raise NotFoundException("Application not found.")
        return packet

    async def _generate(
        self, job: JobForAnalytics, packet: ApplicationPacket, request: GenerationInput
    ) -> ApplicationGeneratedResponse:
        # Questions come exclusively from this request; the source job is never updated.
        job.questions = list(request.questions) if request.generate_answers else []
        job.application = packet
        try:
            result = await self.generator.generate_sync(
                RequestedGeneration(
                    cover_letter=request.generate_cover_letter, questions=request.generate_answers
                ),
                job,
            )
            response = ApplicationGeneratedResponse(debug=result.debug)
            if request.generate_cover_letter:
                if not isinstance(result, RequestedCoverletter) or not result.cover_letter.strip():
                    raise ApplicationGenerationException()
                response.cover_letter = result.cover_letter
            if request.generate_answers:
                if not isinstance(result, RequestedQuestions):
                    raise ApplicationGenerationException()
                if [q.question.strip() for q in result.questions] != request.questions or any(
                    q.answer is None or not q.answer.strip() for q in result.questions
                ):
                    raise ApplicationGenerationException()
                response.questions = [
                    Question(question=text, answer=answer.answer)
                    for text, answer in zip(request.questions, result.questions, strict=True)
                ]
            return response
        except (OpenAIError, ValidationError, ApplicationGenerationException) as error:
            logging.getLogger(__name__).exception("Application generation failed")
            raise ApplicationGenerationException() from error

    async def create_application(
        self, match_id: UUID, request: GenerationInput
    ) -> ApplicationResponse:
        job = await self._job(match_id)
        try:
            packet = await self.classifier.classify(job, self.profile)
        except (OpenAIError, httpx.HTTPError, ValidationError) as error:
            logging.getLogger(__name__).exception("Candidate selection failed")
            raise ApplicationGenerationException() from error
        packet = packet.model_copy(deep=True)
        packet.id, packet.match_id, packet.job_id = 0, match_id, job.id
        response = await self._generate(job, packet, request)
        packet.cover_letter = response.cover_letter
        packet.questions = response.questions or []
        packet.debug = response.debug
        try:
            saved = await self.repository.save_packet(packet)
        except LookupError as error:
            raise NotFoundException("Match not found.") from error
        return application_response(saved)

    async def list_applications(
        self, match_id: UUID, *, limit: int, offset: int
    ) -> ApplicationsResponse:
        try:
            packets, total = await self.repository.list_packets(match_id, limit, offset)
        except LookupError as error:
            raise NotFoundException("Match not found.") from error
        return ApplicationsResponse(
            applications=[application_response(p) for p in packets],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def get_application(self, application_id: int) -> ApplicationResponse:
        return application_response(await self._packet(application_id))

    async def _update(
        self, application_id: int, changes: ApplicationGeneratedResponse
    ) -> ApplicationResponse:
        try:
            saved = await self.repository.update_packet(application_id, changes)
        except LookupError as error:
            raise NotFoundException("Application not found.") from error
        return application_response(saved)

    async def update_application(
        self, application_id: int, patch: ApplicationPatchInput
    ) -> ApplicationResponse:
        changes = ApplicationGeneratedResponse()
        if "cover_letter" in patch.model_fields_set:
            changes.cover_letter = patch.cover_letter
        if "questions" in patch.model_fields_set:
            changes.questions = [
                Question(question=q.question, answer=q.answer) for q in patch.questions
            ]
        async with self._locks.setdefault(application_id, asyncio.Lock()):
            return await self._update(application_id, changes)

    async def regenerate_application(
        self, application_id: int, request: GenerationInput
    ) -> ApplicationResponse:
        async with self._locks.setdefault(application_id, asyncio.Lock()):
            packet = (await self._packet(application_id)).model_copy(deep=True)
            job = await self._job(packet.match_id)
            changes = await self._generate(job, packet, request)
            return await self._update(application_id, changes)
