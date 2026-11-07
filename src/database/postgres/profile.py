"""PostgreSQL profile adapter. Storage rows and JSONB never cross the repository boundary."""

import json
from collections.abc import Mapping
from typing import ClassVar, Self

from asyncpg import Connection, Pool
from pydantic import ConfigDict, field_validator

from src.database.evidence import EvidenceNotFound
from src.database.postgres.owner import owner_transaction
from src.shared.models.evidence import NewEvidence
from src.shared.models.profile import (
    CareerRecordReference,
    EvidenceLinks,
    ProfileUpdate,
    UserProfile,
)


class _ProfileRow(UserProfile):
    """Local storage extension: accept driver JSON strings and bind PostgreSQL values."""

    model_config = ConfigDict(extra="ignore")
    _json_columns: ClassVar[frozenset[str]] = frozenset(
        {
            "profile_links",
            "employment_history",
            "education",
            "languages",
            "work_authorizations",
            "compensation_expectations",
            "resumes",
        }
    )

    @field_validator(
        "profile_links",
        "employment_history",
        "education",
        "languages",
        "work_authorizations",
        "compensation_expectations",
        "resumes",
        mode="before",
    )
    @classmethod
    def decode_json(cls, value: object) -> object:
        if isinstance(value, str):
            decoded: object = json.loads(value)
            return decoded
        return value

    @classmethod
    def from_domain(cls, profile: UserProfile) -> Self:
        return cls.model_validate(profile.model_dump())

    def to_domain(self) -> UserProfile:
        return UserProfile.model_validate(self.model_dump())

    def updated(self, update: ProfileUpdate) -> Self:
        # Include supplied fields without excluding defaults inside nested domain
        # records: generated IDs must survive transport -> domain -> storage.
        values = self.model_dump()
        values.update(update.model_dump(include=update.model_fields_set))
        return type(self).model_validate(values)

    def parameters(self, names: set[str]) -> tuple[list[str], list[object]]:
        values = self.model_dump()
        json_values = self.model_dump(mode="json")
        args: list[object] = [self.id]
        setters = []
        for name in sorted(names):
            value: object = values[name]
            if name in self._json_columns:
                value = json.dumps(json_values[name])
            elif name in {"linkedin_url", "github_url", "portfolio_url"} and value is not None:
                value = str(value)
            args.append(value)
            setters.append(
                f"{name}=${len(args)}" + ("::jsonb" if name in self._json_columns else "")
            )
        return setters, args


def _profile_from_record(row: Mapping[str, object]) -> UserProfile:
    return _ProfileRow.model_validate(dict(row)).to_domain()


class ProfileRepositoryPostgres:
    def __init__(self, pool: Pool) -> None:
        self.pool = pool

    async def _read(self, conn: Connection, user_id: int) -> UserProfile:
        row = await conn.fetchrow("SELECT * FROM users WHERE id=$1", user_id)
        if row is None:
            raise EvidenceNotFound("Profile not found.")
        return _profile_from_record(row)

    async def get_profile(self, user_id: int) -> UserProfile:
        async with owner_transaction(self.pool, user_id) as conn:
            return await self._read(conn, user_id)

    async def _save(
        self, conn: Connection, current: UserProfile, update: ProfileUpdate
    ) -> UserProfile:
        stored = _ProfileRow.from_domain(current).updated(update)
        ids = {
            eid
            for entry in [*stored.employment_history, *stored.education]
            for eid in entry.evidence_ids
        }
        if ids:
            count = await conn.fetchval(
                "SELECT count(*) FROM evidence WHERE user_id=$1 AND id=ANY($2::uuid[])",
                current.id,
                list(ids),
            )
            if count != len(ids):
                raise EvidenceNotFound("One or more attached evidence files were not found.")
        setters, args = stored.parameters(update.model_fields_set)
        row = await conn.fetchrow(
            "UPDATE users SET " + ",".join(setters) + ",updated_at=now() WHERE id=$1 RETURNING *",
            *args,
        )
        if row is None:
            raise EvidenceNotFound("Profile not found.")
        return _profile_from_record(row)

    async def update_profile(self, user_id: int, update: ProfileUpdate) -> UserProfile:
        async with owner_transaction(self.pool, user_id) as conn:
            return await self._save(conn, await self._read(conn, user_id), update)

    @staticmethod
    def _link_update(
        current: UserProfile, target: CareerRecordReference, links: EvidenceLinks
    ) -> ProfileUpdate:
        try:
            return current.evidence_link_update(target, links)
        except LookupError as error:
            raise EvidenceNotFound(str(error)) from error

    async def set_evidence_links(
        self, user_id: int, target: CareerRecordReference, links: EvidenceLinks
    ) -> UserProfile:
        async with owner_transaction(self.pool, user_id) as conn:
            current = await self._read(conn, user_id)
            return await self._save(conn, current, self._link_update(current, target, links))

    async def create_linked_evidence(
        self, user_id: int, target: CareerRecordReference, evidence: NewEvidence
    ) -> UserProfile:
        async with owner_transaction(self.pool, user_id) as conn:
            current = await self._read(conn, user_id)
            try:
                entry = current.career_entry(target)
            except LookupError as error:
                raise EvidenceNotFound(str(error)) from error
            exists = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM folders WHERE user_id=$1 AND path=$2)",
                user_id,
                evidence.folder_path,
            )
            if not exists:
                raise EvidenceNotFound("Folder not found.")
            await conn.execute(
                """INSERT INTO evidence(id,user_id,folder_path,title,text,use_for_matching)
                   VALUES($1,$2,$3,$4,$5,$6)""",
                evidence.id,
                user_id,
                evidence.folder_path,
                evidence.title,
                evidence.text,
                evidence.use_for_matching,
            )
            update = self._link_update(
                current, target, EvidenceLinks(evidence_ids=[*entry.evidence_ids, evidence.id])
            )
            return await self._save(conn, current, update)
