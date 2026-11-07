"""Opt-in application repository for UUID evidence references.

Not imported by analyst wiring. Requires applications.materials UUID[] and user_id;
never migrates the database or falls back to materials_snapshot. Missing/deleted or
foreign-owned evidence fails explicitly rather than silently changing the context.
"""

import json
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from asyncpg import Connection, Pool

from src.database.postgres.application_packet import packet_from_record
from src.database.postgres.match import _MATCH_COLUMNS, matched_job_from_join
from src.database.protocols import ApplicationPacketRepository
from src.shared.models import (
    ApplicationGeneratedResponse,
    ApplicationPacket,
    JobForAnalytics,
    RuntimeVersion,
)


class ApplicationPacketMaterialsRepositoryPostgres(ApplicationPacketRepository):
    def __init__(self, pool: Pool, runtime_ver: RuntimeVersion, *, user_id: int) -> None:
        self.pool = pool
        self.runtime_version = runtime_ver
        self.user_id = user_id

    async def _hydrate(
        self, conn: Connection, rows: list[Mapping[str, Any]]
    ) -> list[ApplicationPacket]:
        ids = list({UUID(str(value)) for row in rows for value in row["materials"]})
        evidence = (
            await conn.fetch(
                "SELECT id,text FROM evidence WHERE user_id=$1 AND id=ANY($2::uuid[])",
                self.user_id,
                ids,
            )
            if ids
            else []
        )
        lookup = {UUID(str(item["id"])): item["text"] for item in evidence}
        packets = []
        for row in rows:
            if row["user_id"] != self.user_id:
                raise LookupError("Application not found.")
            references = [UUID(str(value)) for value in row["materials"]]
            if any(value not in lookup for value in references):
                raise ValueError("Application references missing or inaccessible evidence.")
            # Reuse the established JSON/domain decoder, supplying resolved text only
            # in memory. Neither the database snapshot nor production mapper is changed.
            resolved = dict(row)
            resolved["materials_snapshot"] = [
                {"id": str(value), "text": lookup[value]} for value in references
            ]
            packets.append(packet_from_record(resolved))
        return packets

    async def save_packet(self, packet: ApplicationPacket) -> ApplicationPacket:
        ids = [UUID(item.id) for item in packet.materials]
        if len(set(ids)) != len(ids):
            raise ValueError("Evidence references must be unique.")
        async with self.pool.acquire() as conn, conn.transaction():
            # Coordinate with the evidence repository's owner lock, including deletions.
            if (
                await conn.fetchval("SELECT id FROM users WHERE id=$1 FOR UPDATE", self.user_id)
                is None
            ):
                raise LookupError("User not found.")
            owned = await conn.fetchval(
                "SELECT count(*) FROM evidence WHERE user_id=$1 AND id=ANY($2::uuid[])",
                self.user_id,
                ids,
            )
            if owned != len(ids):
                raise ValueError("Application references missing or inaccessible evidence.")
            row = await conn.fetchrow(
                """INSERT INTO applications
                   (job_id,match_id,materials,user_id,summary,cover_letter,questions,debug,version,iteration)
                   SELECT $1,$2,$3::uuid[],$4,$5,$6,$7::jsonb,$8::jsonb,$9,$10
                   WHERE EXISTS(SELECT 1 FROM matches m JOIN jobs j ON j.id=m.job_id
                       WHERE m.id=$2 AND j.id=$1 AND j.deleted_at IS NULL) RETURNING *""",
                packet.job_id,
                packet.match_id,
                ids,
                self.user_id,
                packet.summary,
                packet.cover_letter,
                json.dumps([q.model_dump() for q in packet.questions]),
                packet.debug,
                self.runtime_version.version,
                self.runtime_version.iteration,
            )
            if row is None:
                raise LookupError("Match not found.")
            saved = (await self._hydrate(conn, [row]))[0]
        packet.id, packet.created_at, packet.updated_at = (
            saved.id,
            saved.created_at,
            saved.updated_at,
        )
        return saved

    async def get_packet_by_id(self, packet_id: int) -> ApplicationPacket | None:
        async with self.pool.acquire() as conn:
            async with conn.transaction(isolation="repeatable_read", readonly=True):
                row = await conn.fetchrow(
                    """SELECT a.* FROM applications a JOIN jobs j ON j.id=a.job_id
                       WHERE a.id=$1 AND a.user_id=$2 AND a.deleted_at IS NULL
                       AND j.deleted_at IS NULL""",
                    packet_id,
                    self.user_id,
                )
                return (await self._hydrate(conn, [row]))[0] if row is not None else None

    async def list_packets(
        self, match_id: UUID, limit: int, offset: int = 0
    ) -> tuple[list[ApplicationPacket], int]:
        async with self.pool.acquire() as conn:
            async with conn.transaction(isolation="repeatable_read", readonly=True):
                if not await conn.fetchval(
                    """SELECT EXISTS(SELECT 1 FROM matches m JOIN jobs j ON j.id=m.job_id
                       WHERE m.id=$1 AND j.deleted_at IS NULL)""",
                    match_id,
                ):
                    raise LookupError("Match not found.")
                total = await conn.fetchval(
                    "SELECT count(*) FROM applications WHERE match_id=$1 AND user_id=$2 AND deleted_at IS NULL",
                    match_id,
                    self.user_id,
                )
                rows = await conn.fetch(
                    """SELECT * FROM applications WHERE match_id=$1 AND user_id=$2 AND deleted_at IS NULL
                       ORDER BY created_at DESC,id DESC LIMIT $3 OFFSET $4""",
                    match_id,
                    self.user_id,
                    limit,
                    offset,
                )
                return await self._hydrate(conn, list(rows)), int(total)

    async def get_pending_packets(self, limit: int = 1) -> list[JobForAnalytics]:
        async with self.pool.acquire() as conn:
            async with conn.transaction(isolation="repeatable_read", readonly=True):
                rows = await conn.fetch(
                    "SELECT "
                    + _MATCH_COLUMNS
                    + """,to_jsonb(pa) AS application_packet
                    FROM applications pa JOIN jobs j ON j.id=pa.job_id
                    JOIN matches m ON m.id=pa.match_id LEFT JOIN companies c ON c.id=j.company_id
                    WHERE pa.user_id=$1 AND pa.updated_at IS NULL AND j.deleted_at IS NULL
                    AND pa.deleted_at IS NULL ORDER BY pa.created_at,pa.id LIMIT $2""",
                    self.user_id,
                    limit,
                )
                raw = [
                    json.loads(row["application_packet"])
                    if isinstance(row["application_packet"], str)
                    else row["application_packet"]
                    for row in rows
                ]
                packets = await self._hydrate(conn, raw)
                jobs = []
                for row, packet in zip(rows, packets, strict=True):
                    job = matched_job_from_join(row)
                    job.application = packet
                    jobs.append(job)
                return jobs

    async def update_packet(
        self, packet_id: int, resp: ApplicationGeneratedResponse
    ) -> ApplicationPacket:
        if not resp.model_fields_set:
            raise ValueError("No application changes supplied.")
        setters = []
        args: list[Any] = [packet_id, self.user_id]
        if "cover_letter" in resp.model_fields_set:
            args.append(resp.cover_letter)
            setters.append(f"cover_letter=${len(args)}::text")
        if "questions" in resp.model_fields_set:
            if resp.questions is None:
                raise ValueError("Questions cannot be null.")
            args.append(json.dumps([q.model_dump() for q in resp.questions]))
            setters.append(f"questions=${len(args)}::jsonb")
        if "debug" in resp.model_fields_set:
            args.append(resp.debug)
            setters.append(
                f"debug=COALESCE(debug,'{{}}'::jsonb) || COALESCE(${len(args)}::jsonb,'{{}}'::jsonb)"
            )
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.fetchval("SELECT id FROM users WHERE id=$1 FOR UPDATE", self.user_id)
            row = await conn.fetchrow(
                "UPDATE applications a SET "
                + ",".join(setters)
                + """,updated_at=now() WHERE a.id=$1 AND a.user_id=$2 AND a.deleted_at IS NULL
                   AND EXISTS(SELECT 1 FROM jobs j WHERE j.id=a.job_id AND j.deleted_at IS NULL)
                   RETURNING a.*""",
                *args,
            )
            if row is None:
                raise LookupError("Application not found.")
            return (await self._hydrate(conn, [row]))[0]
