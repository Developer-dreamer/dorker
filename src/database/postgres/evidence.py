"""Transactional path library, with per-owner locks for folder/evidence mutations."""

import json
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator
from uuid import UUID

from asyncpg import Connection, Pool, UniqueViolationError
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.database.evidence import EvidenceConflict, EvidenceNotFound
from src.database.postgres.owner import owner_transaction
from src.shared.models.evidence import (
    Evidence,
    EvidenceFolder,
    EvidenceUpdate,
    MatchEvidence,
    MatchEvidenceUpdate,
    NewEvidence,
)
from src.shared.models.profile import EducationEntry, EmploymentEntry


class _EvidenceRow(Evidence):
    """Only this adapter knows storage-only owner/check columns."""

    model_config = ConfigDict(extra="ignore")
    user_id: int

    def to_domain(self) -> Evidence:
        return Evidence(
            id=self.id,
            folder_path=self.folder_path,
            title=self.title,
            text=self.text,
            use_for_matching=self.use_for_matching,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class _SelectedEvidenceRow(_EvidenceRow):
    checked: bool


class _ProfileEvidenceRow(BaseModel):
    employment_history: list[EmploymentEntry] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)

    @field_validator("employment_history", "education", mode="before")
    @classmethod
    def decode_json(cls, value: object) -> object:
        if isinstance(value, str):
            decoded: object = json.loads(value)
            return decoded
        return value

    def references(self, evidence_id: UUID) -> list[str]:
        return [
            entry.company or str(entry.id)
            for entry in self.employment_history
            if evidence_id in entry.evidence_ids
        ] + [
            entry.institution or str(entry.id)
            for entry in self.education
            if evidence_id in entry.evidence_ids
        ]


class _MatchContextRow(BaseModel):
    id: UUID
    evidence_initialized_at: datetime | None


class EvidenceRepositoryPostgres:
    def __init__(self, pool: Pool) -> None:
        self.pool = pool

    @asynccontextmanager
    async def _owner(self, user_id: int) -> AsyncIterator[Connection]:
        async with owner_transaction(self.pool, user_id) as conn:
            yield conn

    @staticmethod
    async def _folder(conn: Connection, user_id: int, path: str) -> None:
        if not await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM folders WHERE user_id=$1 AND path=$2)", user_id, path
        ):
            raise EvidenceNotFound("Folder not found.")

    async def list_folders(self, user_id: int) -> list[EvidenceFolder]:
        async with self.pool.acquire() as conn:
            return [
                EvidenceFolder.model_validate(dict(row))
                for row in await conn.fetch(
                    "SELECT path,created_at FROM folders WHERE user_id=$1 ORDER BY path", user_id
                )
            ]

    async def create_folder(self, user_id: int, path: str) -> EvidenceFolder:
        try:
            async with self._owner(user_id) as conn:
                parent = path.rsplit("/", 1)[0]
                if parent:
                    await self._folder(conn, user_id, parent)
                row = await conn.fetchrow(
                    "INSERT INTO folders(user_id,path) VALUES($1,$2) RETURNING path,created_at",
                    user_id,
                    path,
                )
                return EvidenceFolder.model_validate(dict(row))
        except UniqueViolationError as error:
            raise EvidenceConflict("Folder already exists.") from error

    async def delete_folder(self, user_id: int, path: str) -> None:
        async with self._owner(user_id) as conn:
            await self._folder(conn, user_id, path)
            if await conn.fetchval(
                """SELECT EXISTS(SELECT 1 FROM folders WHERE user_id=$1
                   AND starts_with(path,$2 || '/')) OR EXISTS(
                   SELECT 1 FROM evidence WHERE user_id=$1 AND folder_path=$2)""",
                user_id,
                path,
            ):
                raise EvidenceConflict("Delete the folder's evidence and subfolders first.")
            await conn.execute("DELETE FROM folders WHERE user_id=$1 AND path=$2", user_id, path)

    async def list_evidence(
        self, user_id: int, *, folder_path: str | None = None, matching_only: bool = False
    ) -> list[Evidence]:
        async with self.pool.acquire() as conn:
            if folder_path is not None:
                await self._folder(conn, user_id, folder_path)
            rows = await conn.fetch(
                """SELECT * FROM evidence WHERE user_id=$1
                   AND ($2::text IS NULL OR folder_path=$2)
                   AND (NOT $3::boolean OR use_for_matching)
                   ORDER BY folder_path,title,id""",
                user_id,
                folder_path,
                matching_only,
            )
            return [_EvidenceRow.model_validate(dict(row)).to_domain() for row in rows]

    async def get_evidence(self, user_id: int, evidence_id: UUID) -> Evidence:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM evidence WHERE user_id=$1 AND id=$2", user_id, evidence_id
            )
            if row is None:
                raise EvidenceNotFound("Evidence not found.")
            return _EvidenceRow.model_validate(dict(row)).to_domain()

    async def create_evidence(self, user_id: int, evidence: NewEvidence) -> Evidence:
        async with self._owner(user_id) as conn:
            await self._folder(conn, user_id, evidence.folder_path)
            row = await conn.fetchrow(
                """INSERT INTO evidence(id,user_id,folder_path,title,text,use_for_matching)
                   VALUES($1,$2,$3,$4,$5,$6) RETURNING *""",
                evidence.id,
                user_id,
                evidence.folder_path,
                evidence.title,
                evidence.text,
                evidence.use_for_matching,
            )
            return _EvidenceRow.model_validate(dict(row)).to_domain()

    async def update_evidence(
        self, user_id: int, evidence_id: UUID, update: EvidenceUpdate
    ) -> Evidence:
        fields = update.model_dump(include=update.model_fields_set)
        async with self._owner(user_id) as conn:
            setters = [f"{key}=${i}" for i, key in enumerate(fields, start=3)]
            row = await conn.fetchrow(
                "UPDATE evidence SET "
                + ",".join(setters)
                + ",updated_at=now() WHERE user_id=$1 AND id=$2 RETURNING *",
                user_id,
                evidence_id,
                *fields.values(),
            )
            if row is None:
                raise EvidenceNotFound("Evidence not found.")
            return _EvidenceRow.model_validate(dict(row)).to_domain()

    async def delete_evidence(self, user_id: int, evidence_id: UUID) -> None:
        async with self._owner(user_id) as conn:
            row = await conn.fetchrow(
                "SELECT employment_history,education FROM users WHERE id=$1", user_id
            )
            if row is None:
                raise EvidenceNotFound("Profile not found.")
            linked = _ProfileEvidenceRow.model_validate(dict(row)).references(evidence_id)
            if linked:
                raise EvidenceConflict(
                    "Detach this evidence from profile records before deleting: "
                    + ", ".join(linked)
                )
            result = await conn.fetchval(
                "DELETE FROM evidence WHERE user_id=$1 AND id=$2 RETURNING id", user_id, evidence_id
            )
            if result is None:
                raise EvidenceNotFound("Evidence not found.")

    @staticmethod
    async def _match(conn: Connection, match_id: UUID, *, lock: bool = False) -> _MatchContextRow:
        row = await conn.fetchrow(
            """SELECT m.id,m.evidence_initialized_at FROM matches m JOIN jobs j ON j.id=m.job_id
               WHERE m.id=$1 AND j.deleted_at IS NULL"""
            + (" FOR UPDATE OF m" if lock else ""),
            match_id,
        )
        if row is None:
            raise EvidenceNotFound("Match not found.")
        return _MatchContextRow.model_validate(dict(row))

    @staticmethod
    async def _selection(conn: Connection, user_id: int, match_id: UUID) -> MatchEvidence:
        match = await EvidenceRepositoryPostgres._match(conn, match_id)
        rows = await conn.fetch(
            """SELECT e.*,COALESCE(me.checked,false) AS checked FROM evidence e
               LEFT JOIN match_evidence me ON me.evidence_id=e.id AND me.match_id=$2
               WHERE e.user_id=$1 ORDER BY e.folder_path,e.title,e.id""",
            user_id,
            match_id,
        )
        stored = [_SelectedEvidenceRow.model_validate(dict(row)) for row in rows]
        return MatchEvidence(
            match_id=match_id,
            initialized_at=match.evidence_initialized_at,
            evidence=[row.to_domain() for row in stored],
            checked_ids=[row.id for row in stored if row.checked],
        )

    async def get_match_evidence(self, user_id: int, match_id: UUID) -> MatchEvidence:
        async with self.pool.acquire() as conn:
            async with conn.transaction(isolation="repeatable_read", readonly=True):
                return await self._selection(conn, user_id, match_id)

    async def set_match_evidence(
        self, user_id: int, match_id: UUID, update: MatchEvidenceUpdate
    ) -> MatchEvidence:
        checked_ids = update.checked_ids
        async with self._owner(user_id) as conn:
            await self._match(conn, match_id, lock=True)
            owned = await conn.fetchval(
                "SELECT count(*) FROM evidence WHERE user_id=$1 AND id=ANY($2::uuid[])",
                user_id,
                checked_ids,
            )
            if owned != len(checked_ids):
                raise EvidenceNotFound("One or more evidence blocks were not found.")
            # A complete selection replacement, including explicit empty selection.
            await conn.execute(
                "UPDATE match_evidence SET checked=false,updated_at=now() WHERE match_id=$1",
                match_id,
            )
            await conn.executemany(
                """INSERT INTO match_evidence(match_id,evidence_id,checked) VALUES($1,$2,true)
                   ON CONFLICT(match_id,evidence_id) DO UPDATE SET checked=true,updated_at=now()""",
                [(match_id, evidence_id) for evidence_id in checked_ids],
            )
            await conn.execute(
                "UPDATE matches SET evidence_initialized_at=COALESCE(evidence_initialized_at,now()) WHERE id=$1",
                match_id,
            )
            return await self._selection(conn, user_id, match_id)
