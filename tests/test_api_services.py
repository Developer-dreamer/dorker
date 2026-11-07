"""Exercise real API services with synthetic persistence and providers, without network access."""

import asyncio
import copy
import json
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID, uuid4

import httpx
import uuid6
from openai import APIConnectionError

from src.analytics.discussion import DiscussionService as AnalyticsDiscussionService
from src.analytics.discussion import ReplyEvent
from src.analytics.profile_repository_xml import ProfileRepositoryXML
from src.api import create_app
from src.api.models import ApplicationPatchInput, GenerationInput
from src.api.services.application import ApplicationService
from src.api.services.discussion_adapter import DiscussionService, SafeDiscussionModel
from src.api.services.job import JobService
from src.api.services.match import MatchService
from src.shared.models import (
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
    RequestedCoverletter,
    RequestedFullResponse,
    RequestedQuestions,
    SuitabilityTier,
)
from src.shared.scripts.bu import (
    ExtractedJobData,
    LiveJobScraper,
    LoginWallException,
    _is_login_or_challenge_page,
    _render_page_in_context,
)

NOW = datetime(2026, 10, 27, tzinfo=timezone.utc)


class MemoryStore:
    """A domain-model fake of the existing repository contracts."""

    def __init__(self):
        self.match_id = uuid4()
        self.jobs = {
            "opaque": JobForAnalytics(
                id="opaque",
                title="Engineer",
                company=Company(id=7, name="Synthetic Company"),
                location=None,
                description="",
                url="https://example.org/job",
                ats_type="custom",
                questions=[],
                salary_min=None,
                salary_max=None,
                salary_currency=None,
                fetched_at=NOW,
            )
        }
        self.match = MatchedJob(
            id=self.match_id,
            job_id="opaque",
            suitability_tier="SUITABLE",
            technical_capability_score=0.8,
            strategic_value_score=0.7,
            created_at=NOW,
            job_summary=JobAnalytics(summary="Summary", pros="First\n\nSecond", cons="Risk"),
            debug='{"private": true}',
        )
        self.applications = {}
        self.messages = []
        self.last_changes = None
        self.last_filters = None

    async def list_jobs(self, limit, offset):
        rows = list(self.jobs.values())
        return [j.model_copy(deep=True) for j in rows[offset : offset + limit]], len(rows)

    async def get_job_by_id(self, job_id):
        job = self.jobs.get(job_id)
        return job.model_copy(deep=True) if job else None

    async def save_job(self, job: Job) -> JobForAnalytics:
        analytics_job = JobForAnalytics(
            id=job.id,
            title=job.title,
            company=Company(id=job.company_id, name="Unknown"),
            location=job.location,
            description=job.description or "",
            url=str(job.url),
            apply_url=str(job.apply_url) if job.apply_url else None,
            ats_type=job.ats_type.value,
            employment_type=job.employment_type,
            questions=list(job.questions),
            salary_min=job.salary_min,
            salary_max=job.salary_max,
            salary_currency=job.salary_currency,
            posted_at=job.posted_at,
            fetched_at=job.fetched_at or NOW,
        )
        self.jobs[job.id] = analytics_job
        return analytics_job.model_copy(deep=True)

    async def save_match(self, match: MatchedJob) -> None:
        match_copy = match.model_copy(deep=True)
        match_copy.created_at = match_copy.created_at or NOW
        self.match = match_copy
        self.match_id = match_copy.id

    def _require_match(self, match_id):
        if match_id != self.match_id:
            raise LookupError("Match not found.")

    async def get_matches(self, tiers, statuses, limit=50, offset=0):
        self.last_filters = (tiers, statuses)
        jobs = (
            [await self.get_job_by_match_id(self.match_id)]
            if (tiers is None or self.match.suitability_tier in tiers)
            and (statuses is None or self.match.pipeline_status in statuses)
            else []
        )
        return jobs[offset : offset + limit], len(jobs)

    async def get_application_matches(self, tiers, statuses, limit=50, offset=0):
        self.last_filters = (tiers, statuses)
        has_app = any(p.match_id == self.match_id for p in self.applications.values())
        qualifies = (self.match.pipeline_status == "PENDING" and has_app) or (
            self.match.pipeline_status
            in {
                "APPLIED",
                "INTERVIEWING",
                "OFFER",
                "GHOSTED",
                "REJECTED_EARLY",
                "REJECTED_LATE",
                "DECLINED",
            }
        )
        jobs = (
            [await self.get_job_by_match_id(self.match_id)]
            if qualifies
            and (tiers is None or self.match.suitability_tier in tiers)
            and (statuses is None or self.match.pipeline_status in statuses)
            else []
        )
        return jobs[offset : offset + limit], len(jobs)

    async def get_matches_stats(self, pipeline_statuses):
        return MatchCounts(
            suitable=int(
                pipeline_statuses is None or self.match.pipeline_status in pipeline_statuses
            )
        )

    async def report_match(self, match_id, text):
        self._require_match(match_id)
        self.report_message = text

    async def update_pipeline_status(self, match_id, status):
        self._require_match(match_id)
        self.match.pipeline_status = status

    async def get_match_debug(self, match_id):
        self._require_match(match_id)
        return self.match.debug

    async def get_job_by_match_id(self, match_id):
        if match_id != self.match_id:
            return None
        job = self.jobs[self.match.job_id].model_copy(deep=True)
        if self.match.job_id == "opaque":
            job.questions = ["Never use implicitly"]
        job.match = self.match.model_copy(deep=True)
        return job

    async def get_latest_match_by_job_id(self, job_id: str):
        if self.match.job_id != job_id:
            return None
        return await self.get_job_by_match_id(self.match_id)

    async def get_packet_by_id(self, application_id):
        packet = self.applications.get(application_id)
        return packet.model_copy(deep=True) if packet else None

    async def list_packets(self, match_id, limit, offset=0):
        self._require_match(match_id)
        packets = sorted(
            self.applications.values(), key=lambda p: (p.created_at, p.id), reverse=True
        )
        return [p.model_copy(deep=True) for p in packets[offset : offset + limit]], len(packets)

    async def save_packet(self, packet):
        packet.id = len(self.applications) + 1
        packet.created_at = NOW
        self.applications[packet.id] = packet.model_copy(deep=True)
        return packet.model_copy(deep=True)

    async def update_packet(self, application_id, changes):
        packet = await self.get_packet_by_id(application_id)
        if packet is None:
            raise LookupError("Application not found.")
        self.last_changes = changes.model_copy(deep=True)
        for name in changes.model_fields_set:
            setattr(packet, name, copy.deepcopy(getattr(changes, name)))
        packet.updated_at = NOW
        self.applications[application_id] = packet.model_copy(deep=True)
        return packet

    async def save(self, message):
        self.messages.append(message.model_copy(deep=True))

    async def history(self, match_id, user_id, chat_id):
        return [
            m.model_copy(deep=True)
            for m in self.messages
            if (m.match_id, m.user_id, m.chat_id) == (match_id, user_id, chat_id)
        ]


class ServiceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = MemoryStore()
        self.profile = ProfileRepositoryXML(Path(__file__).parent / "fixtures/profile.xml")

        async def classify(job, profile):
            return ApplicationPacket(
                job_id=job.id,
                match_id=job.match.id,
                materials=[Material(id="synthetic", text="Candidate context")],
            )

        self.classifier = SimpleNamespace(classify=AsyncMock(side_effect=classify))

        async def generate(request, job):
            questions = [Question(question=q, answer=" Answer ") for q in job.questions]
            if request.cover_letter and request.questions:
                return RequestedFullResponse(cover_letter=" Letter ", questions=questions)
            if request.questions:
                return RequestedQuestions(questions=questions)
            return RequestedCoverletter(cover_letter=" Letter ")

        self.generator = SimpleNamespace(generate_sync=AsyncMock(side_effect=generate))
        self.applications = ApplicationService(
            self.store, self.store, self.classifier, self.profile, self.generator
        )
        self.model = SimpleNamespace(reply=AsyncMock(return_value=("Reply", {})))
        self.stream_closed = False

        async def stream(messages):
            try:
                yield ReplyEvent(delta="Re")
                yield ReplyEvent(delta="ply")
                yield ReplyEvent(answer="Reply")
            finally:
                self.stream_closed = True

        self.model.stream_reply = stream
        analytics = AnalyticsDiscussionService(
            self.store, SafeDiscussionModel(self.model), self.profile, self.classifier
        )
        self.discussion = DiscussionService(analytics, self.store, self.store, user_id=7, chat_id=7)
        self.summary_available = asyncio.Event()

        async def classify_job(job, profile):
            return MatchedJob(
                job_id=job.id,
                suitability_tier=SuitabilityTier.SUITABLE,
                technical_capability_score=0.85,
                strategic_value_score=0.9,
            )

        self.job_classifier = SimpleNamespace(classify=AsyncMock(side_effect=classify_job))

        async def scrape_url(url: str) -> Job:
            scraped_id = str(uuid6.uuid7())
            return Job(
                id=scraped_id,
                ats_type=ATSType.CUSTOM,
                ats_id=scraped_id,
                url=url,
                apply_url=url,
                title="Scraped Backend Engineer",
                company_id=-1,
                location="Remote",
                country_iso="US",
                region="US",
                employment_type="FULL_TIME",
                description="Build distributed systems in Python and Go.",
                questions=["Why this role?"],
                salary_min=140000.0,
                salary_max=180000.0,
                salary_currency="USD",
                is_normalized=True,
                posted_at=None,
                fetched_at=NOW,
            )

        self.scraper = SimpleNamespace(scrape=AsyncMock(side_effect=scrape_url))
        app = create_app(
            JobService(
                self.store,
                match_repository=self.store,
                classifier=self.job_classifier,
                profile=self.profile,
                scraper=self.scraper,
                summary_available=self.summary_available,
            ),
            match_service=MatchService(self.store),
            discussion_service=self.discussion,
            application_service=self.applications,
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        self.base = f"/api/matches/{self.store.match_id}"

    async def asyncTearDown(self):
        await self.client.aclose()

    async def create(self, **body):
        response = await self.client.post(
            self.base + "/applications", json=body or {"generate_answers": False}
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    async def test_job_match_projections_filters_counts_and_not_found(self):
        result = (await self.client.get("/api/jobs")).json()
        self.assertEqual(result["jobs"][0]["company"], "Synthetic Company")
        self.assertEqual(
            self.store.jobs["opaque"].model_dump(mode="json")["company"],
            {"id": 7, "name": "Synthetic Company"},
        )
        self.assertEqual(result["jobs"][0]["description"], "")
        self.assertEqual(result["jobs"][0]["questions"], [])
        self.assertEqual((await self.client.get("/api/jobs/opaque")).status_code, 200)
        self.assertEqual((await self.client.get("/api/jobs/missing")).status_code, 404)
        page = (await self.client.get("/api/jobs?offset=1")).json()
        self.assertEqual((page["jobs"], page["total"]), ([], 1))
        match = (await self.client.get(self.base)).json()
        self.assertEqual(match["analytics"]["pros"], ["First", "Second"])
        page = (await self.client.get("/api/matches?tiers=RUNWAY&pipeline_status=APPLIED")).json()
        self.assertEqual(page["total"], 0)
        self.assertEqual(self.store.last_filters, (["RUNWAY"], ["APPLIED"]))
        self.assertEqual((await self.client.get("/api/matches")).json()["total"], 1)
        # Pending match without an application packet does not qualify as an application yet:
        self.assertEqual((await self.client.get("/api/matches/applications")).json()["total"], 0)
        counts = (await self.client.get("/api/matches/counts")).json()
        self.assertEqual(len(counts), 4)
        self.assertEqual(counts["SUITABLE"], 1)
        self.assertEqual(
            (await self.client.get(self.base + "/debug")).json(), {"debug": '{"private": true}'}
        )
        self.assertEqual(
            (await self.client.post(self.base + "/reports", json={"text": " Report "})).status_code,
            204,
        )
        self.assertEqual(self.store.report_message, "Report")
        self.assertEqual(
            (
                await self.client.patch(self.base + "/status", json={"status": "APPLIED"})
            ).status_code,
            200,
        )
        # Once status is APPLIED, it qualifies as an application even without an application packet:
        self.assertEqual((await self.client.get("/api/matches/applications")).json()["total"], 1)
        counts = (await self.client.get("/api/matches/counts?pipeline_status=PENDING")).json()
        self.assertEqual(sum(counts.values()), 0)
        self.assertEqual((await self.client.get(f"/api/matches/{uuid4()}")).status_code, 404)
        by_job = await self.client.get("/api/matches/by-job/opaque")
        self.assertEqual(by_job.status_code, 200)
        self.assertEqual(by_job.json()["job_id"], "opaque")
        self.assertEqual((await self.client.get("/api/matches/by-job/missing")).status_code, 404)

    async def test_scrape_job_creates_uuid7_job_with_sentinel_company_and_jev_match(self):
        response = await self.client.post(
            "/api/jobs/scrape", json={"url": "https://example.org/careers/backend-engineer"}
        )
        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        job_data = payload["job"]
        match_data = payload["match"]
        parsed_job_uuid = UUID(job_data["id"])
        self.assertEqual(parsed_job_uuid.version, 7)
        self.assertEqual(job_data["company"], "Unknown")
        self.assertEqual(self.store.jobs[job_data["id"]].company.id, -1)
        self.assertEqual(match_data["job_id"], job_data["id"])
        self.assertEqual(match_data["suitability_tier"], "SUITABLE")
        self.assertTrue(self.summary_available.is_set())
        self.job_classifier.classify.assert_awaited_once()

    async def test_live_job_scraper_helper_extracts_uuid7_and_sentinel_company(self):
        parsed = ExtractedJobData(
            title=" Distributed Systems Engineer ",
            location="Remote, Europe",
            country_iso="de",
            region="EU",
            employment_type="FULL_TIME",
            description="<h2>Role</h2><p>Build <strong>reliable</strong> services.</p>",
            questions=["What distributed systems have you built?"],
            salary_min=120000.0,
            salary_max=160000.0,
            salary_currency="eur",
            apply_url="/apply",
        )
        openai_client = SimpleNamespace(
            responses=SimpleNamespace(
                parse=AsyncMock(
                    return_value=SimpleNamespace(status="completed", output_parsed=parsed)
                )
            ),
            close=AsyncMock(),
        )

        async def fake_fetcher(url: str) -> tuple[str, str]:
            html_doc = (
                "<html><head><title>Job</title></head>"
                "<body><label>What distributed systems have you built?</label>"
                "<main><p>Build reliable services.</p></main></body></html>"
            )
            return html_doc, url

        scraper = LiveJobScraper(client=openai_client, html_fetcher=fake_fetcher)
        job = await scraper.scrape("https://careers.example.com/jobs/42")
        self.assertEqual(UUID(job.id).version, 7)
        self.assertEqual(job.company_id, -1)
        self.assertEqual(job.ats_type, ATSType.CUSTOM)
        self.assertEqual(job.title, "Distributed Systems Engineer")
        self.assertEqual(job.country_iso, "DE")
        self.assertEqual(job.salary_currency, "EUR")
        self.assertEqual(str(job.apply_url), "https://careers.example.com/apply")
        self.assertTrue(job.is_normalized)
        self.assertIn("## Role", job.description or "")
        await scraper.close()
        openai_client.close.assert_awaited_once()

    async def test_login_wall_focuses_tab_waits_for_user_login_and_aborts_if_still_blocked(self):
        # 1. Test interactive tab login wait in _render_page_in_context (Brave CDP / visible tab flow)
        state = {"logged_in": False}
        page = SimpleNamespace(
            url="https://djinni.co/login?from=/jobs/123",
            title=AsyncMock(return_value="Sign in - Djinni"),
            goto=AsyncMock(),
            wait_for_load_state=AsyncMock(),
            wait_for_timeout=AsyncMock(),
            evaluate=AsyncMock(return_value=True),
            content=AsyncMock(return_value="<html><body>Job Description</body></html>"),
            is_closed=Mock(return_value=False),
            close=AsyncMock(),
        )

        async def focus_and_login():
            state["logged_in"] = True
            page.url = "https://djinni.co/jobs/123-backend-engineer"
            page.title.return_value = "Backend Engineer - Djinni"
            page.evaluate.return_value = False

        page.bring_to_front = AsyncMock(side_effect=focus_and_login)
        context = SimpleNamespace(new_page=AsyncMock(return_value=page))

        self.assertTrue(await _is_login_or_challenge_page(page))
        content, final_url, needs_login = await _render_page_in_context(
            context, "https://djinni.co/jobs/123-backend-engineer", 5000, is_visible=True
        )
        page.bring_to_front.assert_awaited_once()
        page.close.assert_awaited_once()
        self.assertFalse(needs_login)
        self.assertEqual(final_url, "https://djinni.co/jobs/123-backend-engineer")
        self.assertIn("Job Description", content)

        # 2. Test LLM guardrail when a page remains blocked by a login wall
        blocked = ExtractedJobData(
            is_login_or_captcha_wall=True,
            title="Sign In",
            description="Please sign in to view this job.",
        )
        openai_client = SimpleNamespace(
            responses=SimpleNamespace(
                parse=AsyncMock(
                    return_value=SimpleNamespace(status="completed", output_parsed=blocked)
                )
            ),
            close=AsyncMock(),
        )

        async def blocked_fetcher(url: str) -> tuple[str, str]:
            return "<html><body>Please sign in</body></html>", url

        scraper = LiveJobScraper(client=openai_client, html_fetcher=blocked_fetcher)
        with self.assertRaises(LoginWallException):
            await scraper.scrape("https://djinni.co/jobs/123")
        await scraper.close()

        # 3. Verify API surfaces the LoginWallException message on 502 without saving a job
        self.scraper.scrape.side_effect = LoginWallException(
            "Job page is behind a login wall or anti-bot challenge."
        )
        with self.assertLogs("src.api.services.job", level="WARNING"):
            resp = await self.client.post(
                "/api/jobs/scrape", json={"url": "https://djinni.co/jobs/123"}
            )
        self.assertEqual(resp.status_code, 502)
        self.assertIn("login wall", resp.json()["detail"])

    async def test_draft_creation_edit_and_selected_regeneration(self):
        first = await self.create(
            generate_cover_letter=True, generate_answers=True, questions=[" Why? "]
        )
        # Once an application packet exists, the PENDING match appears in /api/matches/applications:
        self.assertEqual((await self.client.get("/api/matches/applications")).json()["total"], 1)
        second = await self.create()
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["questions"], [{"question": "Why?", "answer": " Answer "}])
        self.assertIsNone(first["updated_at"])
        page = (await self.client.get(self.base + "/applications?limit=1")).json()
        self.assertEqual(page["total"], 2)
        self.assertEqual(page["applications"][0]["id"], second["id"])
        path = f"/api/applications/{first['id']}"
        edited = (await self.client.patch(path, json={"cover_letter": None})).json()
        self.assertIsNone(edited["cover_letter"])
        self.assertEqual(edited["questions"], first["questions"])
        self.assertEqual(self.store.last_changes.model_fields_set, {"cover_letter"})
        self.assertIsNone(self.store.last_changes.cover_letter)
        edited = (
            await self.client.patch(
                path, json={"questions": [{"question": " Custom ", "answer": " Exact\n "}]}
            )
        ).json()
        self.assertEqual(edited["questions"], [{"question": "Custom", "answer": " Exact\n "}])
        regen = (
            await self.client.post(path + "/generation", json={"generate_answers": False})
        ).json()
        self.assertEqual(regen["cover_letter"], " Letter ")
        self.assertEqual(regen["questions"], edited["questions"])
        regen = (
            await self.client.post(
                path + "/generation",
                json={"generate_cover_letter": False, "questions": ["New question"]},
            )
        ).json()
        self.assertEqual(regen["cover_letter"], " Letter ")
        self.assertEqual(regen["questions"], [{"question": "New question", "answer": " Answer "}])
        self.assertEqual(
            self.classifier.classify.await_count, 2
        )  # Regeneration reuses saved materials.
        self.assertEqual((await self.client.get(path)).json(), regen)
        self.assertEqual((await self.client.get("/api/applications/999")).status_code, 404)
        self.assertEqual(
            (await self.client.get(f"/api/matches/{uuid4()}/applications")).status_code, 404
        )

    async def test_generation_failures_do_not_create_or_change_drafts(self):
        draft = await self.create()
        path = f"/api/applications/{draft['id']}"
        original = copy.deepcopy(self.store.applications)
        self.generator.generate_sync.side_effect = APIConnectionError(
            request=httpx.Request("POST", "https://example.org")
        )
        with self.assertLogs("src.api.services.application", level="ERROR"):
            response = await self.client.post(
                path + "/generation", json={"generate_answers": False}
            )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json(), {"detail": "Application generation failed."})
        self.assertEqual(self.store.applications, original)
        with self.assertLogs("src.api.services.application", level="ERROR"):
            response = await self.client.post(
                self.base + "/applications", json={"generate_answers": False}
            )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.store.applications, original)
        self.generator.generate_sync.side_effect = ValueError("private implementation error")
        with self.assertLogs("src.api.handlers.application", level="ERROR"):
            response = await self.client.post(
                path + "/generation", json={"generate_answers": False}
            )
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("private", response.text)

    async def test_regeneration_serializes_edits_without_holding_a_database_transaction(self):
        draft = await self.create()
        entered, release = asyncio.Event(), asyncio.Event()

        async def generate(request, job):
            entered.set()
            await release.wait()
            return RequestedCoverletter(cover_letter="Generated")

        self.generator.generate_sync.side_effect = generate
        regen = asyncio.create_task(
            self.applications.regenerate_application(
                draft["id"], GenerationInput(generate_answers=False)
            )
        )
        await asyncio.wait_for(entered.wait(), 1)
        edit = asyncio.create_task(
            self.applications.update_application(
                draft["id"], ApplicationPatchInput(cover_letter="Manual")
            )
        )
        await asyncio.sleep(0)
        self.assertFalse(edit.done())
        release.set()
        await asyncio.wait_for(asyncio.gather(regen, edit), 1)
        self.assertEqual(
            (await self.applications.get_application(draft["id"])).cover_letter, "Manual"
        )

    async def test_discussion_lifecycle_visible_history_streaming_and_identity(self):
        path = self.base + "/discussion"
        self.assertEqual(
            (await self.client.get(path)).json(), {"state": "not_started", "messages": []}
        )
        self.assertEqual(
            (await self.client.post(path + "/messages", json={"text": "Hi"})).status_code, 409
        )
        self.assertEqual((await self.client.post(path + "/start")).status_code, 204)
        self.assertEqual((await self.client.post(path + "/start")).status_code, 409)
        self.assertEqual((await self.client.get(path)).json(), {"state": "open", "messages": []})
        reply = (await self.client.post(path + "/messages", json={"text": "Hi"})).json()
        self.assertEqual(reply["user_message"]["content"], "Hi")
        self.assertEqual(reply["assistant_message"]["content"], "Reply")
        response = await self.client.post(path + "/stream", json={"text": "Again"})
        events = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual([e["type"] for e in events], ["accepted", "delta", "delta", "done"])
        self.assertTrue(self.stream_closed)
        history = (await self.client.get(path)).json()["messages"]
        self.assertEqual(len(history), 4)
        self.assertEqual(history[-1], events[-1]["message"])
        self.assertNotIn("metadata", history[0])
        self.assertEqual(await self.store.history(self.store.match_id, 8, 8), [])
        self.assertEqual((await self.client.post(path + "/finish")).status_code, 204)
        self.assertEqual((await self.client.post(path + "/finish")).status_code, 409)
        self.assertEqual((await self.client.post(path + "/start")).status_code, 409)
        self.assertEqual((await self.client.get(path)).json()["state"], "finished")
        self.assertEqual(
            (await self.client.get(f"/api/matches/{uuid4()}/discussion")).status_code, 404
        )

    async def test_failed_reply_retains_user_and_stream_reports_saved(self):
        path = self.base + "/discussion"
        await self.client.post(path + "/start")
        self.model.reply.side_effect = APIConnectionError(
            request=httpx.Request("POST", "https://example.org")
        )
        with self.assertLogs("src.api.services.discussion_adapter", level="ERROR"):
            response = await self.client.post(path + "/messages", json={"text": "Hi"})
        self.assertEqual(response.status_code, 502)
        self.assertTrue(response.json()["saved"])
        self.assertEqual((await self.client.get(path)).json()["messages"][0]["content"], "Hi")

        async def failing(messages):
            yield ReplyEvent(delta="partial")
            raise ValueError("provider secret")

        self.model.stream_reply = failing
        with self.assertLogs("src.api.services.discussion_adapter", level="ERROR"):
            response = await self.client.post(path + "/stream", json={"text": "Again"})
        events = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual([e["type"] for e in events], ["accepted", "delta", "error"])
        self.assertTrue(events[-1]["saved"])
        self.assertNotIn("secret", response.text)
        self.assertEqual((await self.client.post(path + "/finish")).status_code, 204)

    async def test_wrong_question_answers_are_rejected_without_saving(self):
        self.generator.generate_sync.side_effect = None
        self.generator.generate_sync.return_value = RequestedQuestions(
            questions=[Question(question="Different question", answer="Wrong attachment")]
        )
        with self.assertLogs("src.api.services.application", level="ERROR"):
            response = await self.client.post(
                self.base + "/applications",
                json={"generate_cover_letter": False, "questions": ["Original question"]},
            )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.store.applications, {})

    async def test_closing_real_stream_closes_provider_and_allows_finish(self):
        await self.discussion.start(self.store.match_id)
        events = await self.discussion.stream_message(self.store.match_id, "Question")
        self.assertEqual((await anext(events)).type, "accepted")
        self.assertEqual((await anext(events)).type, "delta")
        await events.aclose()
        self.assertTrue(self.stream_closed)
        await asyncio.wait_for(self.discussion.finish(self.store.match_id), 1)
        state = await self.discussion.get_discussion(self.store.match_id)
        self.assertEqual(state.state, "finished")
        self.assertEqual(len(state.messages), 1)  # Provisional output was not saved as a reply.


class EntryPointTests(unittest.IsolatedAsyncioTestCase):
    async def test_entry_point_injects_all_services_and_closes_providers_on_server_failure(self):
        from contextlib import ExitStack

        from orchestration import analyst

        @asynccontextmanager
        async def pool(**kwargs):
            yield Mock()

        @asynccontextmanager
        async def typesafe():
            yield Mock()

        generator = SimpleNamespace(close=AsyncMock())
        discussion_model = SimpleNamespace(close=AsyncMock())
        summary = SimpleNamespace(close=AsyncMock())
        scraper = SimpleNamespace(close=AsyncMock())
        with ExitStack() as stack:
            for name in [
                "ProfileRepositoryXML",
                "JobRepositoryPostgres",
                "JobFactSheetRepositoryPostgres",
                "MatchRepositoryPostgres",
                "ApplicationPacketRepositoryPostgres",
                "ClassifyJobTierJev",
                "ClassifyProfileToJob",
                "MatchingEngine",
                "DiscussionRepositoryPostgres",
                "SummaryWorker",
            ]:
                stack.enter_context(patch.object(analyst, name))
            stack.enter_context(patch.object(analyst.asyncpg, "create_pool", side_effect=pool))
            stack.enter_context(patch.object(analyst, "AsyncTypeSafeClient", side_effect=typesafe))
            stack.enter_context(
                patch.object(analyst, "define_iteration", AsyncMock(return_value=1))
            )
            stack.enter_context(patch.object(analyst, "OpenAIClient", return_value=generator))
            stack.enter_context(
                patch.object(analyst, "OpenAIDiscussionModel", return_value=discussion_model)
            )
            stack.enter_context(
                patch.object(analyst, "OpenAISummaryGenerator", return_value=summary)
            )
            stack.enter_context(patch.object(analyst, "LiveJobScraper", return_value=scraper))
            factory = stack.enter_context(patch.object(analyst, "create_app", wraps=create_app))
            stack.enter_context(
                patch.object(
                    analyst, "run_services", AsyncMock(side_effect=RuntimeError("Server stopped"))
                )
            )
            with self.assertRaisesRegex(RuntimeError, "Server stopped"):
                await analyst.run()
            args, kwargs = factory.call_args
            self.assertIsInstance(args[0], JobService)
            self.assertIsInstance(kwargs["match_service"], MatchService)
            self.assertIsInstance(kwargs["discussion_service"], DiscussionService)
            self.assertIsInstance(kwargs["application_service"], ApplicationService)
        for provider in [generator, discussion_model, summary, scraper]:
            provider.close.assert_awaited_once()
