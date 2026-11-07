"""Offline profile models, API, service, and repository checks. No database connections."""

import json
import re
import unittest
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from litestar.testing import TestClient
from pydantic import ValidationError

from src.api.app import create_app
from src.api.evidence_models import EvidenceCreate
from src.api.profile_models import EvidenceLinksInput, ProfilePatch
from src.api.services.profile import ProfileService, ProfileServiceProtocol
from src.database.evidence import EvidenceNotFound
from src.database.postgres.evidence import EvidenceRepositoryPostgres
from src.database.postgres.profile import ProfileRepositoryPostgres
from src.database.profile import ProfileRepository
from src.shared.models.evidence import NewEvidence
from src.shared.models.profile import (
    CareerCollection,
    CareerRecordReference,
    CompensationExpectation,
    EducationEntry,
    EmploymentEntry,
    EvidenceLinks,
    LanguageEntry,
    ProfileUpdate,
    ResumeEntry,
    UserProfile,
    WorkAuthorization,
)

NOW = datetime.now(timezone.utc)


def profile(**fields):
    return UserProfile(id=17, created_at=NOW, updated_at=NOW, **fields)


def connection(row):
    @asynccontextmanager
    async def transaction():
        try:
            yield
        except Exception:
            conn.rollbacks += 1
            raise
        else:
            conn.commits += 1

    conn = SimpleNamespace(
        fetchrow=AsyncMock(return_value=row),
        fetchval=AsyncMock(return_value=17),
        fetch=AsyncMock(return_value=[]),
        execute=AsyncMock(),
        transaction=transaction,
        commits=0,
        rollbacks=0,
    )

    @asynccontextmanager
    async def acquire():
        yield conn

    return conn, SimpleNamespace(acquire=acquire)


class ProfileValidationTests(unittest.TestCase):
    def test_explicit_availability_and_date_invariants(self):
        self.assertIsNone(profile().availability)
        immediate = profile(availability="immediately", notice_period_days=30)
        self.assertEqual(immediate.availability, "immediately")
        self.assertIsNone(immediate.earliest_start_date)
        self.assertEqual(immediate.notice_period_days, 30)
        # An earliest date is a lower bound, so historical dates remain valid.
        self.assertEqual(
            profile(availability="from_date", earliest_start_date=date(2020, 1, 1)).availability,
            "from_date",
        )
        for fields in [
            {"availability": "from_date"},
            {"availability": "immediately", "earliest_start_date": "2030-01-01"},
            {"earliest_start_date": "2030-01-01"},
            {"availability": "asap"},
        ]:
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                profile(**fields)
        for choice in ("immediately", None):
            update = ProfileUpdate(availability=choice)
            self.assertEqual(update.model_fields_set, {"availability", "earliest_start_date"})
            self.assertIsNone(update.earliest_start_date)
        with self.assertRaises(ValidationError):
            ProfileUpdate(availability="immediately", earliest_start_date=date(2030, 1, 1))

    def test_empty_profile_and_tristate(self):
        data = profile().model_dump(mode="json")
        self.assertIsNone(data["willing_to_relocate"])
        self.assertEqual(data["education"], [])
        p = profile(
            work_authorizations=[
                {"country": "UA", "authorized_as_employee": True},
                {"country": "US", "authorized_as_employee": False},
            ]
        )
        self.assertTrue(p.work_authorizations[0].authorized_as_employee)
        self.assertFalse(p.work_authorizations[1].authorized_as_employee)
        self.assertIsNone(p.work_authorizations[0].sponsorship_needed_now)
        with self.assertRaises(ValidationError):
            profile(work_authorizations=[{"country": "EU"}])
        with self.assertRaises(ValidationError):
            profile(work_authorizations=[{"country": "US"}, {"country": "US"}])

    def test_legacy_contract_and_null_vs_omitted(self):
        patch = ProfilePatch(name=None, profile_links=["https://example.com"])
        self.assertEqual(patch.model_fields_set, {"name", "profile_links"})
        self.assertNotIn("first_name", patch.model_dump(exclude_unset=True))
        self.assertIsNone(patch.name)
        self.assertEqual(profile(name="One unsplit display name").first_name, None)
        for values in [
            {},
            {"id": 19},
            {"skills": None},
            {"email": "invalid"},
            {"phone": "12345"},
            {"timezone": "Not/A/Timezone"},
            {"notice_period_days": -1},
            {"education": [{"extra": True}]},
        ]:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                ProfilePatch(**values)
        self.assertEqual(ProfilePatch(phone="+380 (12) 345-67-89").phone, "+380123456789")

    def test_dates_currency_and_record_ids(self):
        entry = EducationEntry(institution="Synthetic school")
        self.assertEqual(entry.id.version, 7)
        self.assertEqual(EducationEntry.model_validate(entry.model_dump()).id, entry.id)
        for values in [
            {"education": [{"start_month": "2024-12", "end_month": "2024-01"}]},
            {"education": [{"start_month": "2024-13"}]},
            {"employment_history": [{"is_current": True, "end_month": "2024-01"}]},
            {
                "compensation_expectations": [
                    {
                        "currency": "USD",
                        "minimum": 100,
                        "maximum": 50,
                        "period": "monthly",
                        "basis": "gross",
                    }
                ]
            },
            {"compensation_expectations": [{"minimum": 1, "period": "annual", "basis": "gross"}]},
        ]:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                ProfilePatch(**values)
        for values in [
            {"education": [entry, entry]},
            {"default_resume_id": uuid4()},
            {"resumes": [{"label": "CV", "url": "not a URL"}]},
        ]:
            with self.assertRaises(ValidationError):
                profile(**values)
        resume_id = uuid4()
        p = profile(
            resumes=[{"id": resume_id, "label": "Backend", "url": "https://example.com/cv.pdf"}],
            default_resume_id=resume_id,
        )
        self.assertEqual(p.default_resume_id, resume_id)

    def test_duplicate_links_rejected(self):
        eid = uuid4()
        with self.assertRaises(ValidationError):
            EvidenceLinksInput(evidence_ids=[eid, eid])
        with self.assertRaises(ValidationError):
            EducationEntry(evidence_ids=[eid, eid])


class ProfileApiTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock(spec=ProfileServiceProtocol)
        self.service.get_profile.return_value = profile()
        self.service.update_profile.return_value = profile(name="User")
        self.service.set_evidence_links.return_value = profile()
        self.service.create_linked_evidence.return_value = profile()
        self.client = TestClient(
            create_app(profile_service=self.service), raise_server_exceptions=False
        )

    def test_read_legacy_patch_and_error_contract(self):
        response = self.client.get("/api/profile")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["first_name"])
        self.assertEqual(
            self.client.patch("/api/profile", json={"name": None, "profile_links": []}).status_code,
            200,
        )
        self.assertEqual(
            self.service.update_profile.call_args.args[0].model_fields_set,
            {"name", "profile_links"},
        )
        for payload in [
            {"id": 100},
            {"work_authorizations": [{"country": "EU"}]},
            {"education": None},
        ]:
            self.assertEqual(self.client.patch("/api/profile", json=payload).status_code, 422)
        self.service.update_profile.side_effect = ValueError(
            "Default resume must reference a saved entry."
        )
        self.assertEqual(self.client.patch("/api/profile", json={"resumes": []}).status_code, 422)
        self.assertEqual(
            TestClient(create_app(), raise_server_exceptions=False).get("/api/profile").status_code,
            503,
        )

    def test_options_and_link_actions(self):
        options = self.client.get("/api/profile/options").json()
        self.assertIn("UA", options["countries"])
        self.assertNotIn("EU", options["countries"])
        eid = uuid4()
        entry = uuid4()
        url = f"/api/profile/education/{entry}"
        self.assertEqual(
            self.client.patch(
                url + "/evidence-links", json={"evidence_ids": [str(eid)]}
            ).status_code,
            200,
        )
        self.service.set_evidence_links.assert_awaited_once_with(
            CareerRecordReference(collection=CareerCollection.EDUCATION, entry_id=entry),
            EvidenceLinks(evidence_ids=[eid]),
        )
        payload = {"folder_path": "/education", "title": "Story", "text": "Authored evidence"}
        response = self.client.post(url + "/evidence", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(
            self.client.patch(
                "/api/profile/skills/" + str(entry) + "/evidence-links", json={"evidence_ids": []}
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.patch(
                "/api/profile/education/invalid/evidence-links", json={"evidence_ids": []}
            ).status_code,
            422,
        )
        self.service.set_evidence_links.side_effect = EvidenceNotFound("Record not found.")
        self.assertEqual(
            self.client.patch(url + "/evidence-links", json={"evidence_ids": []}).status_code, 404
        )


class ProfileServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_identity_and_partial_update(self):
        repo = Mock(spec=ProfileRepository)
        service = ProfileService(repo, user_id=17)
        update = ProfileUpdate(first_name="Only first name")
        await service.update_profile(update)
        repo.update_profile.assert_awaited_once_with(17, update)
        self.assertIs(repo.update_profile.call_args.args[1], update)
        entry = uuid4()
        await service.create_linked_evidence(
            CareerRecordReference(collection=CareerCollection.EDUCATION, entry_id=entry),
            EvidenceCreate(folder_path="/school", title="Story", text="Text"),
        )
        args = repo.create_linked_evidence.call_args.args
        self.assertEqual(args[0], 17)
        self.assertEqual(
            args[1], CareerRecordReference(collection=CareerCollection.EDUCATION, entry_id=entry)
        )
        self.assertIsInstance(args[2], NewEvidence)
        self.assertEqual(args[2].id.version, 7)


class ProfileRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_availability_choice_clears_saved_date_without_changing_notice(self):
        for choice in ("immediately", None):
            with self.subTest(choice=choice):
                current = profile(
                    availability="from_date",
                    earliest_start_date=date(2030, 1, 1),
                    notice_period_days=14,
                )
                conn, pool = connection(current.model_dump())
                saved = {
                    **current.model_dump(),
                    "availability": choice,
                    "earliest_start_date": None,
                }
                conn.fetchrow.side_effect = [current.model_dump(), saved]
                result = await ProfileRepositoryPostgres(pool).update_profile(
                    17, ProfileUpdate(availability=choice)
                )
                sql, *args = conn.fetchrow.call_args.args
                self.assertIn("availability=$2", sql)
                self.assertIn("earliest_start_date=$3", sql)
                self.assertNotIn("notice_period_days=", sql)
                self.assertEqual(args, [17, choice, None])
                self.assertIsNone(result.earliest_start_date)
                self.assertEqual(result.notice_period_days, 14)

    async def test_specific_date_requires_complete_valid_merged_availability(self):
        for current, update in [
            (profile(availability="immediately"), ProfileUpdate(availability="from_date")),
            (
                profile(availability="from_date", earliest_start_date=date(2030, 1, 1)),
                ProfileUpdate(earliest_start_date=None),
            ),
            (profile(), ProfileUpdate(earliest_start_date=date(2030, 1, 1))),
        ]:
            conn, pool = connection(current.model_dump())
            with self.assertRaises(ValidationError):
                await ProfileRepositoryPostgres(pool).update_profile(17, update)
            self.assertEqual(conn.fetchrow.await_count, 1)  # Read only; no UPDATE.

    async def test_specific_date_and_unrelated_section_patch_persist(self):
        current = profile(availability="from_date", earliest_start_date=date(2030, 1, 1))
        conn, pool = connection(current.model_dump())
        conn.fetchrow.side_effect = [current.model_dump(), {**current.model_dump(), "name": "New"}]
        result = await ProfileRepositoryPostgres(pool).update_profile(17, ProfileUpdate(name="New"))
        self.assertEqual(result.availability, "from_date")
        self.assertEqual(result.earliest_start_date, date(2030, 1, 1))
        self.assertNotIn("availability=", conn.fetchrow.call_args.args[0])

    async def test_decode_and_update_only_supplied_columns(self):
        initial = profile(name="Keep", skills=["Python"], willing_to_relocate=False).model_dump()
        conn, pool = connection(initial)
        changed = {**initial, "first_name": "New"}
        conn.fetchrow.side_effect = [initial, changed]
        repo = ProfileRepositoryPostgres(pool)
        result = await repo.update_profile(17, ProfileUpdate(first_name="New"))
        self.assertEqual(result.skills, ["Python"])
        self.assertFalse(result.willing_to_relocate)
        sql, *args = conn.fetchrow.call_args.args
        self.assertIn("first_name=$2", sql)
        self.assertNotIn("skills=", sql)
        self.assertEqual(args, [17, "New"])

    async def test_storage_json_is_returned_as_nested_domain_records(self):
        original = profile(
            availability="from_date",
            earliest_start_date=date(2030, 1, 1),
            employment_history=[{"company": "Synthetic", "start_month": "2029-01"}],
            education=[{"institution": "Synthetic School"}],
            languages=[{"language": "English", "proficiency": "fluent"}],
            work_authorizations=[{"country": "US", "authorized_as_employee": False}],
            compensation_expectations=[
                {"currency": "USD", "period": "hourly", "basis": "gross", "minimum": 25}
            ],
            resumes=[{"label": "CV", "url": "https://example.com/cv.pdf"}],
        )
        raw = original.model_dump()
        serialized = original.model_dump(mode="json")
        for column in (
            "profile_links",
            "employment_history",
            "education",
            "languages",
            "work_authorizations",
            "compensation_expectations",
            "resumes",
        ):
            raw[column] = json.dumps(serialized[column])
        raw["storage_only"] = "never exposed"
        _, pool = connection(raw)
        result = await ProfileRepositoryPostgres(pool).get_profile(17)
        self.assertIs(type(result), UserProfile)
        self.assertEqual(result, original)
        for record, expected in (
            (result.employment_history[0], EmploymentEntry),
            (result.education[0], EducationEntry),
            (result.languages[0], LanguageEntry),
            (result.work_authorizations[0], WorkAuthorization),
            (result.compensation_expectations[0], CompensationExpectation),
            (result.resumes[0], ResumeEntry),
        ):
            self.assertIs(type(record), expected)
        self.assertNotIn("storage_only", result.model_dump())

    async def test_nested_identity_is_preserved_when_encoding_update(self):
        current = profile(name="Keep", skills=["Python"])
        update = ProfileUpdate(education=[EducationEntry(institution="Synthetic School")])
        entry = update.education[0]
        conn, pool = connection(current.model_dump())
        stored = current.model_dump()

        async def fetchrow(sql, *args):
            if sql.startswith("UPDATE"):
                stored["education"] = json.loads(args[1])
            return stored

        conn.fetchrow.side_effect = fetchrow
        result = await ProfileRepositoryPostgres(pool).update_profile(17, update)
        written = json.loads(conn.fetchrow.call_args.args[2])
        self.assertEqual(written[0]["id"], str(entry.id))
        self.assertEqual(result.education[0].id, entry.id)
        self.assertEqual(result.name, "Keep")
        self.assertEqual(result.skills, ["Python"])

    async def test_explicit_null_clears_only_supplied_scalar(self):
        current = profile(first_name="Synthetic", last_name="Keep", willing_to_relocate=False)
        conn, pool = connection(current.model_dump())
        conn.fetchrow.side_effect = [
            current.model_dump(),
            {**current.model_dump(), "first_name": None},
        ]
        result = await ProfileRepositoryPostgres(pool).update_profile(
            17, ProfileUpdate(first_name=None)
        )
        self.assertIsNone(result.first_name)
        self.assertEqual(result.last_name, "Keep")
        self.assertFalse(result.willing_to_relocate)
        sql, *args = conn.fetchrow.call_args.args
        self.assertIn("first_name=$2", sql)
        self.assertNotIn("last_name=", sql)
        self.assertNotIn("willing_to_relocate=", sql)
        self.assertEqual(args, [17, None])

    async def test_default_resume_cross_patch_validation_precedes_update(self):
        rid = uuid4()
        initial = profile(
            resumes=[{"id": rid, "label": "CV", "url": "https://example.com/cv"}],
            default_resume_id=rid,
        ).model_dump()
        conn, pool = connection(initial)
        with self.assertRaises(ValidationError):
            await ProfileRepositoryPostgres(pool).update_profile(17, ProfileUpdate(resumes=[]))
        self.assertEqual(conn.fetchrow.await_count, 1)

    async def test_invalid_attachment_and_missing_entry_do_not_write(self):
        initial = profile(education=[{"institution": "School"}]).model_dump()
        entry = initial["education"][0]["id"]
        conn, pool = connection(initial)
        conn.fetchval.side_effect = [17, 0]
        with self.assertRaises(EvidenceNotFound):
            await ProfileRepositoryPostgres(pool).set_evidence_links(
                17,
                CareerRecordReference(collection=CareerCollection.EDUCATION, entry_id=entry),
                EvidenceLinks(evidence_ids=[uuid4()]),
            )
        self.assertEqual(conn.fetchrow.await_count, 1)
        conn, pool = connection(initial)
        with self.assertRaises(EvidenceNotFound):
            await ProfileRepositoryPostgres(pool).create_linked_evidence(
                17,
                CareerRecordReference(collection=CareerCollection.EDUCATION, entry_id=uuid4()),
                NewEvidence(folder_path="/school", title="Story", text="Text"),
            )
        self.assertEqual(conn.execute.await_count, 1)  # owner bootstrap only; no evidence inserted

    async def test_linked_evidence_deletion_is_rejected(self):
        from src.database.evidence import EvidenceConflict

        eid = uuid4()
        row = {
            "employment_history": json.dumps(
                [{"id": str(uuid4()), "company": "Synthetic", "evidence_ids": [str(eid)]}]
            ),
            "education": [],
        }
        conn, pool = connection(row)
        with self.assertRaisesRegex(EvidenceConflict, "Synthetic"):
            await EvidenceRepositoryPostgres(pool).delete_evidence(17, eid)
        self.assertEqual(conn.fetchval.await_count, 1)  # owner lock only, no DELETE

    async def test_create_and_link_uses_same_transaction(self):
        current = profile(education=[{"institution": "School"}])
        entry = current.education[0].id
        eid = uuid4()
        conn, pool = connection(current.model_dump())
        conn.fetchval.side_effect = [17, True, 1]
        final = current.model_copy(deep=True)
        final.education[0].evidence_ids = [eid]
        conn.fetchrow.side_effect = [current.model_dump(), final.model_dump()]
        result = await ProfileRepositoryPostgres(pool).create_linked_evidence(
            17,
            CareerRecordReference(collection=CareerCollection.EDUCATION, entry_id=entry),
            NewEvidence(id=eid, folder_path="/school", title="Story", text="Text"),
        )
        self.assertEqual(result.education[0].evidence_ids, [eid])
        self.assertIn("INSERT INTO evidence", conn.execute.call_args.args[0])
        self.assertIn("education=$2::jsonb", conn.fetchrow.call_args.args[0])

    async def test_failed_create_link_propagates_out_of_transaction(self):
        current = profile(education=[{"institution": "School"}])
        conn, pool = connection(current.model_dump())
        conn.fetchval.side_effect = [17, True, 0]
        with self.assertRaises(EvidenceNotFound):
            await ProfileRepositoryPostgres(pool).create_linked_evidence(
                17,
                CareerRecordReference(
                    collection=CareerCollection.EDUCATION, entry_id=current.education[0].id
                ),
                NewEvidence(folder_path="/school", title="Story", text="Text"),
            )
        self.assertIn("INSERT INTO evidence", conn.execute.call_args.args[0])
        self.assertEqual(conn.rollbacks, 1)
        self.assertEqual(conn.commits, 0)


class MigrationPreparationTests(unittest.TestCase):
    def test_every_profile_field_has_a_reviewable_column(self):
        from pathlib import Path

        from src.shared.models.profile import ProfileDetails

        sql = (
            Path(__file__).resolve().parents[1]
            / "migrations/postgres/sql/012_create_evidence_table.sql"
        ).read_text()
        availability_sql = (
            Path(__file__).resolve().parents[1]
            / "migrations/postgres/sql/013_profile_availability.sql"
        ).read_text()
        for name in ProfileDetails.model_fields:
            self.assertRegex(
                sql + availability_sql,
                rf"\b{re.escape(name)}\s+(?:TEXT|CHAR|INTEGER|DATE|BOOLEAN|JSONB|UUID)\b",
            )
        self.assertIn("BEGIN;", sql)
        self.assertTrue(sql.rstrip().endswith("COMMIT;"))
        self.assertNotIn("INSERT INTO", sql)
