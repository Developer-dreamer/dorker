"""Offline repository contracts: driver rows and stored JSON stop at this boundary."""

import json
import re
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from src.analytics.engine import MatchingEngine
from src.database.postgres.application_packet import ApplicationPacketRepositoryPostgres
from src.database.postgres.job import JobRepositoryPostgres
from src.database.postgres.match import MatchRepositoryPostgres, analytics_from_storage
from src.shared.models import (
    ApplicationGeneratedResponse,
    ApplicationPacket,
    ATSType,
    Company,
    Job,
    JobAnalytics,
    JobForAnalytics,
    MatchCounts,
    MatchedJob,
    Material,
    Question,
    RuntimeVersion,
    SuitabilityTier,
)

NOW = datetime(2026, 10, 29, tzinfo=timezone.utc)
RUNTIME = RuntimeVersion(model="synthetic", version="0.3.1", min_version="0.3.0", iteration=2)


def job_row():
    return dict(
        id="opaque",
        title="Engineer",
        location=None,
        description="Posting",
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        questions=None,
        company_id=7,
        company_name="Synthetic Company",
        url="https://example.org/job",
        apply_url=None,
        ats_type="custom",
        employment_type=None,
        posted_at=NOW,
        fetched_at=NOW,
    )


def joined_row(summary='{"summary":"Scope","pros":"Growth","cons":"Risk"}'):
    return dict(
        **job_row(),
        match_id=uuid4(),
        match_job_id="opaque",
        technical_capability_score=0.8,
        strategic_value_score=0.9,
        suitability_tier="SUITABLE",
        pipeline_status="PENDING",
        job_summary=summary,
        rejection_reason=None,
        match_debug={"raw": True},
        match_created_at=NOW,
    )


def packet_row():
    return dict(
        id=5,
        job_id="opaque",
        match_id=uuid4(),
        summary=None,
        cover_letter=" Authored ",
        questions='["Legacy question", {"question":"Modern question","answer":" Exact "}]',
        materials='[{"id":"synthetic","text":"Candidate material"}]',
        debug={"provider": "synthetic"},
        created_at=NOW,
        updated_at=None,
        version="0.3.1",
        iteration=2,
        deleted_at=None,
    )


def connection(*, rows=None, row=None, scalar=None):
    @asynccontextmanager
    async def transaction(**kwargs):
        yield

    return SimpleNamespace(
        fetch=AsyncMock(return_value=rows or []),
        fetchrow=AsyncMock(return_value=row),
        fetchval=AsyncMock(return_value=scalar),
        execute=AsyncMock(return_value="UPDATE 1"),
        transaction=Mock(side_effect=transaction),
    )


def pool(conn):
    @asynccontextmanager
    async def acquire():
        yield conn

    return SimpleNamespace(acquire=acquire)


class JobDomainRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_hydrates_company_with_one_page_query_and_consistent_total(self):
        conn = connection(rows=[job_row()], scalar=9)
        repo = JobRepositoryPostgres(pool(conn), RUNTIME)
        jobs, total = await repo.list_jobs(50, 0)
        self.assertEqual(total, 9)
        self.assertIsInstance(jobs[0], JobForAnalytics)
        self.assertEqual(jobs[0].company, Company(id=7, name="Synthetic Company"))
        self.assertEqual(jobs[0].questions, [])
        self.assertEqual(jobs[0].fetched_at, NOW)
        conn.fetch.assert_awaited_once()
        conn.fetchrow.assert_not_awaited()
        query, *args = conn.fetch.call_args.args
        self.assertIn("JOIN companies", query)
        self.assertEqual(args, [50, 0])
        conn.transaction.assert_called_once_with(isolation="repeatable_read", readonly=True)
        conn.fetch.return_value = []
        self.assertEqual(await repo.list_jobs(50, 500), ([], 9))

    async def test_lookup_missing_and_nullable_company(self):
        row = job_row()
        row["company_name"] = None
        conn = connection(row=row)
        repo = JobRepositoryPostgres(pool(conn), RUNTIME)
        self.assertIsNone((await repo.get_job_by_id("opaque")).company)
        conn.fetchrow.return_value = None
        self.assertIsNone(await repo.get_job_by_id("missing"))

    async def test_classification_read_retains_filters_and_hydrates_company(self):
        conn = connection(rows=[job_row()])
        repo = JobRepositoryPostgres(pool(conn), RUNTIME)
        jobs = await repo.get_matched_jobs(limit=10)
        self.assertEqual(jobs[0].company.id, 7)
        query, *args = conn.fetch.call_args.args
        self.assertIn("JOIN companies", query)
        self.assertIn("NOT EXISTS", query)
        self.assertIn("j.is_normalized = TRUE", query)
        self.assertEqual(args[0], "0.3.0")
        self.assertEqual(args[-1], 10)

    async def test_save_job_ensures_sentinel_company_and_returns_hydrated_job(self):
        row = job_row()
        row["id"] = "019a0000-0000-7000-8000-000000000001"
        row["company_id"] = -1
        row["company_name"] = "Unknown"
        conn = connection(row=row)
        repo = JobRepositoryPostgres(pool(conn), RUNTIME)
        new_job = Job(
            id=row["id"],
            ats_type=ATSType.CUSTOM,
            ats_id=row["id"],
            url="https://example.org/job",
            apply_url="https://example.org/job/apply",
            title="Engineer",
            company_id=-1,
            location="Remote",
            country_iso="US",
            region="US",
            employment_type="FULL_TIME",
            description="Normalized posting",
            questions=["Why here?"],
            salary_min=100000.0,
            salary_max=150000.0,
            salary_currency="USD",
            is_normalized=True,
            posted_at=None,
            fetched_at=NOW,
        )
        saved = await repo.save_job(new_job)
        self.assertEqual(saved.id, row["id"])
        self.assertEqual(saved.company, Company(id=-1, name="Unknown"))
        query, *args = conn.fetchrow.call_args.args
        self.assertIn("INSERT INTO companies", query)
        self.assertIn("VALUES (-1, 'external'", query)
        self.assertIn("INSERT INTO jobs", query)
        self.assertEqual(args[0], row["id"])
        self.assertEqual(args[6], -1)


class MatchDomainRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def test_structured_legacy_and_absent_analytics(self):
        model = JobAnalytics(summary="Scope", pros="Growth", cons="Risk")
        for raw in [model, model.model_dump(), model.model_dump_json()]:
            self.assertEqual(analytics_from_storage(raw), model)
        for text in [" Historical prose ", "{not valid JSON}", '["legacy text"]', '"quoted text"']:
            self.assertEqual(
                analytics_from_storage(text), JobAnalytics(summary=text, pros="", cons="")
            )
        for raw in [None, "", "  "]:
            self.assertIsNone(analytics_from_storage(raw))

    async def test_list_returns_models_and_filters_after_latest_selection(self):
        conn = connection(rows=[joined_row()], scalar=8)
        repo = MatchRepositoryPostgres(pool(conn), RUNTIME)
        jobs, total = await repo.get_matches(
            [SuitabilityTier.SUITABLE], ["PENDING"], limit=10, offset=2
        )
        self.assertEqual(total, 8)
        self.assertIsInstance(jobs[0].match, MatchedJob)
        self.assertIsInstance(jobs[0].match.job_summary, JobAnalytics)
        self.assertEqual(jobs[0].match.created_at, NOW)
        self.assertEqual(jobs[0].company.id, 7)
        query, *args = conn.fetch.call_args.args
        count_query, *count_args = conn.fetchval.call_args.args
        self.assertEqual(args, ["0.3.0", ["SUITABLE"], ["PENDING"], 10, 2])
        self.assertEqual(args[:3], count_args)
        for sql, parameters in [(query, args), (count_query, count_args)]:
            self.assertLess(sql.index("rn=1"), sql.index("$2::text[]"))
            self.assertNotIn("m.model=", sql)
            self.assertIn("ltrim(m.version, 'v')::semver >= $1::semver", sql)
            self.assertEqual(
                {int(index) for index in re.findall(r"\$(\d+)", sql)},
                set(range(1, len(parameters) + 1)),
            )
            self.assertIn("m.id DESC", sql)
        self.assertIn("LIMIT $4 OFFSET $5", query)
        conn.transaction.assert_called_once_with(isolation="repeatable_read", readonly=True)
        conn.fetch.return_value = []
        self.assertEqual(await repo.get_matches(None, None, offset=100), ([], 8))

    async def test_counts_reuse_latest_selection_and_return_zero_tiers(self):
        conn = connection(rows=[dict(suitability_tier="RUNWAY", total=3)])
        repo = MatchRepositoryPostgres(pool(conn), RUNTIME)
        counts = await repo.get_matches_stats(["PENDING"])
        self.assertEqual(counts, MatchCounts(runway=3))
        self.assertEqual(counts.for_tier(SuitabilityTier.REJECTED), 0)
        query, *args = conn.fetch.call_args.args
        self.assertLess(query.index("rn=1"), query.index("$2::text[]"))
        self.assertEqual(args, ["0.3.0", ["PENDING"]])
        self.assertEqual(
            {int(index) for index in re.findall(r"\$(\d+)", query)},
            set(range(1, len(args) + 1)),
        )
        await repo.get_matches_stats(None)
        self.assertIsNone(conn.fetch.call_args.args[-1])

    async def test_detail_and_legacy_navigation_normalize_plain_summary(self):
        row = joined_row("Old summary")
        conn = connection(row=row)
        repo = MatchRepositoryPostgres(pool(conn), RUNTIME)
        for job in [
            await repo.get_job_by_match_id(row["match_id"]),
            await repo.get_latest_match_by_job_id(row["match_job_id"]),
            await repo.get_matched_job([SuitabilityTier.SUITABLE]),
        ]:
            self.assertEqual(job.match.job_summary, JobAnalytics(summary="Old summary"))
            self.assertEqual(job.company.name, "Synthetic Company")
        conn.fetchrow.return_value = None
        self.assertIsNone(await repo.get_job_by_match_id(uuid4()))
        self.assertIsNone(await repo.get_latest_match_by_job_id("missing"))

    async def test_save_and_summary_update_encode_models_in_repository(self):
        conn = connection()
        repo = MatchRepositoryPostgres(pool(conn), RUNTIME)
        analytics = JobAnalytics(summary="Scope", pros="Growth", cons="Risk")
        match = MatchedJob(job_id="opaque", job_summary=analytics)
        await repo.save_match(match)
        self.assertEqual(json.loads(conn.execute.call_args.args[6]), analytics.model_dump())
        await repo.update_summary(match.id, analytics)
        self.assertEqual(conn.execute.call_args.args[1], match.id)
        self.assertEqual(json.loads(conn.execute.call_args.args[-1]), analytics.model_dump())

    async def test_missing_updates_raise_lookup_error(self):
        conn = connection(scalar=None)
        repo = MatchRepositoryPostgres(pool(conn), RUNTIME)
        with self.assertRaises(LookupError):
            await repo.update_pipeline_status(uuid4(), "PENDING")
        conn.execute.return_value = "UPDATE 0"
        with self.assertRaises(LookupError):
            await repo.report_match(uuid4(), "Report")


class ApplicationDomainRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_lookup_decodes_materials_questions_and_timestamps(self):
        conn = connection(row=packet_row())
        repo = ApplicationPacketRepositoryPostgres(pool(conn), RUNTIME)
        packet = await repo.get_packet_by_id(5)
        self.assertIsInstance(packet, ApplicationPacket)
        self.assertEqual(packet.created_at, NOW)
        self.assertEqual(packet.materials, [Material(id="synthetic", text="Candidate material")])
        self.assertEqual(
            packet.questions,
            [
                Question(question="Legacy question", answer=None),
                Question(question="Modern question", answer=" Exact "),
            ],
        )
        self.assertEqual(json.loads(packet.debug), {"provider": "synthetic"})
        conn.fetchrow.return_value = None
        self.assertIsNone(await repo.get_packet_by_id(999))

    async def test_save_returns_persisted_packet_and_retains_input_id_assignment(self):
        stored = packet_row()
        conn = connection(row=stored)
        repo = ApplicationPacketRepositoryPostgres(pool(conn), RUNTIME)
        packet = ApplicationPacket(
            job_id="opaque",
            match_id=stored["match_id"],
            materials=[Material(id="x", text="Material")],
            cover_letter=" Completed ",
            questions=[Question(question="Question", answer="Answer")],
        )
        saved = await repo.save_packet(packet)
        self.assertEqual(packet.id, saved.id)
        self.assertEqual(saved.created_at, NOW)
        query, *args = conn.fetchrow.call_args.args
        self.assertIn("cover_letter", query)
        self.assertIn("RETURNING *", query)
        self.assertEqual(args[4], " Completed ")
        self.assertEqual(json.loads(args[2]), [{"id": "x", "text": "Material"}])
        self.assertEqual(json.loads(args[5]), [{"question": "Question", "answer": "Answer"}])
        conn.fetchrow.return_value = None
        with self.assertRaises(LookupError):
            await repo.save_packet(packet)

    async def test_partial_update_preserves_omission_and_supports_clear_and_replacement(self):
        conn = connection(row=packet_row())
        repo = ApplicationPacketRepositoryPostgres(pool(conn), RUNTIME)
        await repo.update_packet(5, ApplicationGeneratedResponse(cover_letter=None))
        query, *args = conn.fetchrow.call_args.args
        self.assertIn("cover_letter=", query)
        self.assertNotIn("questions=", query)
        self.assertEqual(args, [5, None])
        await repo.update_packet(5, ApplicationGeneratedResponse(questions=[]))
        query, *args = conn.fetchrow.call_args.args
        self.assertIn("questions=", query)
        self.assertNotIn("cover_letter=", query)
        self.assertEqual(args, [5, "[]"])
        conn.fetchrow.return_value = None
        with self.assertRaises(LookupError):
            await repo.update_packet(999, ApplicationGeneratedResponse(cover_letter="x"))

    async def test_list_and_empty_pages_return_packets_and_full_total(self):
        conn = connection(rows=[packet_row()])
        conn.fetchval.side_effect = [True, 7]
        repo = ApplicationPacketRepositoryPostgres(pool(conn), RUNTIME)
        packets, total = await repo.list_packets(uuid4(), 1, 0)
        self.assertEqual(total, 7)
        self.assertIsInstance(packets[0], ApplicationPacket)
        self.assertIn("ORDER BY created_at DESC, id DESC", conn.fetch.call_args.args[0])
        conn.fetchval.side_effect = [True, 7]
        conn.fetch.return_value = []
        self.assertEqual(await repo.list_packets(uuid4(), 1, 100), ([], 7))
        conn.fetchval.side_effect = [False]
        with self.assertRaises(LookupError):
            await repo.list_packets(uuid4(), 1, 0)

    async def test_pending_packets_hydrate_company_match_and_saved_application_together(self):
        row = joined_row("Legacy summary")
        stored = packet_row()
        stored["match_id"] = str(row["match_id"])
        stored["created_at"] = NOW.isoformat()
        row["application_packet"] = json.dumps(stored)
        conn = connection(rows=[row])
        repo = ApplicationPacketRepositoryPostgres(pool(conn), RUNTIME)
        jobs = await repo.get_pending_packets(10)
        self.assertIsInstance(jobs[0].application, ApplicationPacket)
        self.assertEqual(jobs[0].company.id, 7)
        self.assertEqual(jobs[0].application.created_at, NOW)
        conn.fetch.assert_awaited_once()
        self.assertIn("JOIN companies", conn.fetch.call_args.args[0])
        conn.fetchrow.assert_not_awaited()


class RepositoryCallerTests(unittest.IsolatedAsyncioTestCase):
    async def test_engine_passes_filters_and_unpacks_page(self):
        engine = MatchingEngine.__new__(MatchingEngine)
        engine.logger = Mock()
        engine.match_repo = SimpleNamespace(
            get_matches=AsyncMock(return_value=([], 3)),
            get_matches_stats=AsyncMock(return_value=MatchCounts(suitable=2)),
        )
        await engine._classify_profile_jev()
        engine.match_repo.get_matches.assert_awaited_once_with(
            tiers=[SuitabilityTier.SUITABLE, SuitabilityTier.RUNWAY, SuitabilityTier.STRETCH],
            statuses=["PENDING"],
            limit=1000,
            offset=0,
        )
        self.assertEqual((await engine.get_matching_count())["SUITABLE"], 2)
        engine.match_repo.get_matches_stats.assert_awaited_once_with(pipeline_statuses=["PENDING"])
