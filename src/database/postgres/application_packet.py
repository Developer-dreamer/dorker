import json
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from asyncpg import Pool

from src.database.postgres.match import _MATCH_COLUMNS, debug_from_storage, matched_job_from_join
from src.database.protocols import ApplicationPacketRepository
from src.shared.models import (
    ApplicationGeneratedResponse,
    ApplicationPacket,
    JobForAnalytics,
    Material,
    Question,
    RuntimeVersion,
)


def packet_from_record(row: Mapping[str, Any]) -> ApplicationPacket:
    """Map the production JSON snapshot to materials; ignore trial UUID references."""
    data = {
        name: row[name]
        for name in ApplicationPacket.model_fields
        if name in row and name != "materials"
    }
    questions = data.get("questions") or []
    materials = row["materials_snapshot"] or []
    if isinstance(questions, str):
        questions = json.loads(questions)
    if isinstance(materials, str):
        materials = json.loads(materials)
    parsed = []
    for question in questions:
        if isinstance(question, str):
            try:
                decoded = json.loads(question)
            except json.JSONDecodeError:
                decoded = None
            question = (
                decoded
                if isinstance(decoded, dict)
                else {
                    "question": question,
                    "answer": None,
                }
            )
        parsed.append(Question.model_validate(question))
    data["questions"] = parsed
    data["materials"] = [
        Material.model_validate_json(m) if isinstance(m, str) else Material.model_validate(m)
        for m in materials
    ]
    data["debug"] = debug_from_storage(data.get("debug"))
    return ApplicationPacket.model_validate(data)


class ApplicationPacketRepositoryPostgres(ApplicationPacketRepository):
    def __init__(self, pool: Pool, runtime_ver: RuntimeVersion) -> None:
        self.runtime_version = runtime_ver
        self.pool = pool

    async def save_packet(self, packet: ApplicationPacket) -> ApplicationPacket:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO applications
                   (job_id,match_id,materials_snapshot,summary,cover_letter,questions,debug,version,iteration)
                   SELECT $1,$2,$3::jsonb,$4,$5,$6::jsonb,$7::jsonb,$8,$9
                   WHERE EXISTS(SELECT 1 FROM matches m JOIN jobs j ON j.id=m.job_id
                                WHERE m.id=$2 AND j.id=$1 AND j.deleted_at IS NULL)
                   RETURNING *""",
                packet.job_id,
                packet.match_id,
                json.dumps([m.model_dump() for m in packet.materials]),
                packet.summary,
                packet.cover_letter,
                json.dumps([q.model_dump() for q in packet.questions]),
                packet.debug,
                self.runtime_version.version,
                self.runtime_version.iteration,
            )
        if row is None:
            raise LookupError("Match not found.")
        saved = packet_from_record(row)
        # Older generation callers use the supplied packet's assigned ID for a later update.
        packet.id = saved.id
        packet.created_at = saved.created_at
        packet.updated_at = saved.updated_at
        return saved

    async def get_packet_by_id(self, packet_id: int) -> ApplicationPacket | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT a.* FROM applications a JOIN jobs j ON j.id=a.job_id
                   WHERE a.id=$1 AND a.deleted_at IS NULL AND j.deleted_at IS NULL""",
                packet_id,
            )
        return packet_from_record(row) if row is not None else None

    async def list_packets(
        self, match_id: UUID, limit: int, offset: int = 0
    ) -> tuple[list[ApplicationPacket], int]:
        async with self.pool.acquire() as conn:
            async with conn.transaction(isolation="repeatable_read", readonly=True):
                exists = await conn.fetchval(
                    """SELECT EXISTS(SELECT 1 FROM matches m JOIN jobs j ON j.id=m.job_id
                       WHERE m.id=$1 AND j.deleted_at IS NULL)""",
                    match_id,
                )
                if not exists:
                    raise LookupError("Match not found.")
                total = await conn.fetchval(
                    "SELECT count(*) FROM applications WHERE match_id=$1 AND deleted_at IS NULL",
                    match_id,
                )
                rows = await conn.fetch(
                    """SELECT * FROM applications WHERE match_id=$1 AND deleted_at IS NULL
                       ORDER BY created_at DESC, id DESC LIMIT $2 OFFSET $3""",
                    match_id,
                    limit,
                    offset,
                )
        return [packet_from_record(row) for row in rows], int(total)

    async def get_pending_packets(self, limit: int = 1) -> list[JobForAnalytics]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT "
                + _MATCH_COLUMNS
                + """, to_jsonb(pa) AS application_packet
                FROM applications pa JOIN jobs j ON j.id=pa.job_id
                JOIN matches m ON m.id=pa.match_id LEFT JOIN companies c ON c.id=j.company_id
                WHERE pa.updated_at IS NULL AND j.deleted_at IS NULL AND pa.deleted_at IS NULL
                LIMIT $1""",
                limit,
            )
        jobs = []
        for row in rows:
            job = matched_job_from_join(row)
            raw = row["application_packet"]
            job.application = packet_from_record(json.loads(raw) if isinstance(raw, str) else raw)
            jobs.append(job)
        return jobs

    async def update_packet(
        self, packet_id: int, resp: ApplicationGeneratedResponse
    ) -> ApplicationPacket:
        fields = resp.model_fields_set
        if not fields:
            raise ValueError("No application changes supplied.")
        setters = []
        args: list[Any] = [packet_id]
        if "cover_letter" in fields:
            args.append(resp.cover_letter)
            setters.append(f"cover_letter=${len(args)}::text")
        if "questions" in fields:
            if resp.questions is None:
                raise ValueError("Questions cannot be null.")
            args.append(json.dumps([q.model_dump() for q in resp.questions]))
            setters.append(f"questions=${len(args)}::jsonb")
        if "debug" in fields:
            args.append(resp.debug)
            setters.append(
                "debug=COALESCE(debug, '{}'::jsonb) || "
                f"COALESCE(${len(args)}::jsonb, '{{}}'::jsonb)"
            )
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE applications a SET "
                + ", ".join(setters)
                + """, updated_at=CURRENT_TIMESTAMP WHERE a.id=$1 AND a.deleted_at IS NULL
                AND EXISTS(SELECT 1 FROM jobs j WHERE j.id=a.job_id AND j.deleted_at IS NULL)
                RETURNING a.*""",
                *args,
            )
        if row is None:
            raise LookupError("Application not found.")
        return packet_from_record(row)
