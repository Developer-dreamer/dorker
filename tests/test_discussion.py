import asyncio
import json
import logging
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from aiogram.types import CallbackQuery, Chat, Message, User

from src.analytics.classification.classify_profile_to_job import ClassifyProfileToJob
from src.analytics.discussion import DiscussionService
from src.analytics.engine import MatchingEngine
from src.analytics.generation.discussion_openai import OpenAIDiscussionModel
from src.analytics.generation.openai import OpenAIClient
from src.analytics.profile_repository_xml import ProfileRepositoryXML
from src.client.telegram import TelegramBot
from src.shared.models import (
    ApplicationGeneratedResponse,
    ApplicationPacket,
    JobForAnalytics,
    MatchedJob,
    Material,
    RequestedCoverletter,
    RequestedGeneration,
)


def job(name="job"):
    return JobForAnalytics(
        id=name,
        title="Python developer",
        location="Remote US",
        description="Open to hire globally. Two years of Python required.",
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        match=MatchedJob(job_id=name, debug='{"eligibility": false}'),
    )


def profile_classifier():
    async def classify(target, profile):
        return ApplicationPacket(
            job_id=target.id,
            match_id=target.match.id,
            materials=profile.get_experience(),
            debug='{"selected": true}',
        )

    return SimpleNamespace(classify=AsyncMock(side_effect=classify))


class MemoryRepository:
    def __init__(self):
        self.messages = []

    async def save(self, message):
        self.messages.append(message.model_copy(deep=True))

    async def history(self, match_id, user_id, chat_id):
        return [
            m.model_copy(deep=True)
            for m in self.messages
            if (m.match_id, m.user_id, m.chat_id) == (match_id, user_id, chat_id)
        ]


class DiscussionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repo = MemoryRepository()
        self.profile = ProfileRepositoryXML(Path(__file__).parent / "fixtures/profile.xml")
        self.model = SimpleNamespace(reply=AsyncMock(return_value=("Answer", {})))
        self.service = DiscussionService(
            self.repo,
            self.model,
            self.profile,
            profile_classifier(),
            history_messages=3,
            history_chars=2000,
        )
        self.job = job()
        self.match_id = self.job.match.id
        await self.service.start(self.job, 1, 1)

    async def test_context_window_keeps_confirmations_and_complete_turns(self):
        await self.service.send(
            self.match_id, 1, 1, "I have three years of Python.", confirmed=True
        )
        for index in range(5):
            await self.service.send(self.match_id, 1, 1, f"Question {index}")
        inputs = self.model.reply.call_args.args[0]
        self.assertIn("Candidate in Ukraine", inputs[0]["content"])
        self.assertNotIn("eligibility", inputs[0]["content"])
        self.assertIn("I have three years of Python.", inputs[1]["content"])
        self.assertEqual([m["role"] for m in inputs[2:]], ["user", "assistant", "user"])
        self.assertNotIn("Question 0", json.dumps(inputs))
        self.assertEqual(len(self.repo.messages), 13)
        confirmed = await self.service.confirmed_materials(self.match_id, 1, 1)
        self.assertEqual([m.text for m in confirmed], ["I have three years of Python."])

    async def test_match_and_user_isolation(self):
        other = job("other")
        await self.service.start(other, 1, 1)
        await self.service.send(self.match_id, 1, 1, "Private correction", confirmed=True)
        await self.service.send(other.match.id, 1, 1, "Other job")
        self.assertNotIn("Private correction", json.dumps(self.model.reply.call_args.args[0]))
        self.assertEqual(await self.service.confirmed_materials(self.match_id, 2, 1), [])
        with self.assertRaisesRegex(ValueError, "No discussion"):
            await self.service.send(self.match_id, 2, 1, "Wrong user")

    async def test_finish_prevents_more_messages_and_reopening(self):
        await self.service.finish(self.match_id, 1, 1)
        with self.assertRaisesRegex(ValueError, "finished"):
            await self.service.send(self.match_id, 1, 1, "One more")
        with self.assertRaisesRegex(ValueError, "already has"):
            await self.service.start(self.job, 1, 1)
        self.model.reply.assert_not_awaited()

    async def test_api_failure_saves_user_confirmation_without_fake_answer(self):
        self.model.reply.side_effect = RuntimeError("API unavailable")
        with self.assertRaises(RuntimeError):
            await self.service.send(self.match_id, 1, 1, "Confirmed fact", confirmed=True)
        self.assertEqual(len(self.repo.messages), 2)
        self.assertEqual(
            (await self.service.confirmed_materials(self.match_id, 1, 1))[0].text, "Confirmed fact"
        )
        self.model.reply.side_effect = None
        await self.service.send(self.match_id, 1, 1, "Please try again")
        self.assertEqual(self.repo.messages[-1].role, "assistant")

    async def test_finish_waits_for_inflight_answer(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_reply(_):
            entered.set()
            await release.wait()
            return "Answer", {}

        self.model.reply.side_effect = slow_reply
        send = asyncio.create_task(self.service.send(self.match_id, 1, 1, "Question"))
        await entered.wait()
        finish = asyncio.create_task(self.service.finish(self.match_id, 1, 1))
        await asyncio.sleep(0)
        self.assertFalse(finish.done())
        release.set()
        await asyncio.gather(send, finish)
        self.assertEqual(
            [m.role for m in self.repo.messages], ["user", "user", "assistant", "user"]
        )

    async def test_response_output_replayed_with_reasoning_and_phase(self):
        output = [
            {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "encrypted"},
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": "Answer", "annotations": []}],
            },
        ]
        self.model.reply.return_value = ("Answer", {"output": output})
        await self.service.send(self.match_id, 1, 1, "First")
        await self.service.send(self.match_id, 1, 1, "Second")
        inputs = self.model.reply.call_args.args[0]
        self.assertEqual(inputs[2:4], output)

    async def test_application_merge_preserves_manual_selection_and_records_source(self):
        old = Material(id="experience.project", text="Old profile block")
        manual = Material(id="experience.project", text="User-selected profile block")
        note = Material(id="discussion.note", text="User correction")
        packet = ApplicationPacket(
            job_id=self.job.id,
            match_id=self.match_id,
            materials=[old],
            debug='{"classifier": "original"}',
        )
        repo = SimpleNamespace(save_packet=AsyncMock(), update_packet=AsyncMock())
        generator = SimpleNamespace(
            generate_sync=AsyncMock(return_value=RequestedCoverletter(cover_letter="Letter"))
        )
        engine = MatchingEngine(
            logging.getLogger("test"),
            None,
            None,
            None,
            None,
            repo,
            job_clf=None,
            profile_clf=SimpleNamespace(classify=AsyncMock(return_value=packet)),
            profile=self.profile,
            application_generator=generator,
        )
        await engine.generate_application_for_match(
            RequestedGeneration(questions=False),
            self.job,
            materials=[manual, note],
        )
        self.assertEqual(packet.materials, [manual, note])
        self.assertEqual(json.loads(packet.debug)["classifier"], "original")
        self.assertEqual(
            json.loads(packet.debug)["user_confirmed_materials"],
            [manual.model_dump(), note.model_dump()],
        )
        repo.save_packet.assert_awaited_once_with(packet)
        # Verify the existing generator receives explicitly identified corrections.
        client = OpenAIClient.__new__(OpenAIClient)
        client.master_prompt = "Write a letter."
        instructions, content, _ = client._resolve_generation_objective(
            RequestedGeneration(questions=False), self.job
        )
        self.assertIn("precedence", instructions)
        self.assertIn("User correction", content)

    async def test_discussion_only_receives_selected_materials_and_compact_context(self):
        await self.service.send(self.match_id, 1, 1, "Is this role suitable?")
        payload = json.loads(self.model.reply.call_args.args[0][0]["content"])
        self.assertEqual(payload["selected_materials"][0]["text"], "Relevant Python experience")
        self.assertNotIn("Irrelevant academic course", json.dumps(payload))
        self.assertNotIn("debug", payload["jev_assessment"])
        self.assertNotIn("match", payload["job"])
        self.assertNotIn("description_blocks", payload["job"])
        self.assertEqual(self.repo.messages[0].metadata["match_debug"], self.job.match.debug)

    async def test_legacy_discussion_can_fall_back_to_normal_generation_selection(self):
        self.repo.messages[0].metadata.pop("application_packet")
        self.assertIsNone(await self.service.selected_packet(self.match_id, 1, 1))

    async def test_generation_reuses_selection_without_second_classification(self):
        packet = await self.service.selected_packet(self.match_id, 1, 1)
        classifier = profile_classifier()
        repository = SimpleNamespace(save_packet=AsyncMock(), update_packet=AsyncMock())
        generator = SimpleNamespace(
            generate_sync=AsyncMock(return_value=RequestedCoverletter(cover_letter="Letter"))
        )
        engine = MatchingEngine(
            logging.getLogger("test"),
            None,
            None,
            None,
            None,
            repository,
            job_clf=None,
            profile_clf=classifier,
            profile=self.profile,
            application_generator=generator,
        )
        addition = Material(id="academic.unselected", text="Manually added course")
        await engine.generate_application_for_match(
            RequestedGeneration(questions=False),
            self.job,
            materials=[addition],
            selected_packet=packet,
        )
        classifier.classify.assert_not_awaited()
        self.assertEqual(self.job.application.materials, packet.materials + [addition])
        self.assertEqual(len(packet.materials), 1)
        with self.assertRaisesRegex(ValueError, "different job"):
            await engine.generate_application_for_match(
                RequestedGeneration(questions=False),
                job("another"),
                selected_packet=packet,
            )

    async def test_profile_classification_receives_only_profile_and_description(self):
        from unittest.mock import Mock

        classifier = ClassifyProfileToJob.__new__(ClassifyProfileToJob)
        classifier.client = SimpleNamespace(system_one=AsyncMock(return_value=object()))
        classifier._classify_local = Mock(
            return_value=ApplicationPacket(
                job_id=self.job.id,
                match_id=self.match_id,
            )
        )
        self.job.application = ApplicationPacket(
            job_id=self.job.id, match_id=self.match_id, debug="APPLICATION_DEBUG"
        )
        self.job.description_blocks = [("DERIVED_DESCRIPTION_BLOCK", 0.9, "derived")]
        await classifier.classify(self.job, self.profile)
        state = classifier.client.system_one.call_args.kwargs["state"]
        self.assertIn(self.profile.full_dump(), state)
        self.assertIn(self.job.description, state)
        self.assertNotIn("APPLICATION_DEBUG", state)
        self.assertNotIn("eligibility", state)
        self.assertNotIn("DERIVED_DESCRIPTION_BLOCK", state)


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_history_and_output_metadata(self):
        output = {
            "type": "message",
            "role": "assistant",
            "phase": "final_answer",
            "content": [{"type": "output_text", "text": "Answer"}],
        }
        response = SimpleNamespace(
            status="completed",
            id="resp_1",
            model="configured-model",
            usage=None,
            output_text="Answer",
            output=[SimpleNamespace(model_dump=lambda **_: output)],
        )
        client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(return_value=response)))
        provider = OpenAIDiscussionModel("configured-model", client=client)
        answer, metadata = await provider.reply([{"role": "user", "content": "Question"}])
        self.assertEqual(answer, "Answer")
        self.assertEqual(metadata["output"], [output])
        kwargs = client.responses.create.call_args.kwargs
        self.assertFalse(kwargs["store"])
        self.assertEqual(kwargs["include"], ["reasoning.encrypted_content"])
        self.assertEqual(kwargs["model"], "configured-model")


class TelegramTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.job = job()
        self.repo = MemoryRepository()
        self.profile = ProfileRepositoryXML(Path(__file__).parent / "fixtures/profile.xml")
        self.service = DiscussionService(
            self.repo,
            SimpleNamespace(reply=AsyncMock(return_value=("Answer", {}))),
            self.profile,
            profile_classifier(),
        )
        self.engine = SimpleNamespace(
            get_job_by_match_id=AsyncMock(return_value=self.job),
            get_matched_job=AsyncMock(return_value=None),
            get_matching_count=AsyncMock(return_value={"SUITABLE": 2, "STRETCH": 1}),
            append_question=AsyncMock(),
            update_pipeline_status=AsyncMock(),
            report_match=AsyncMock(),
            generate_application_for_match=AsyncMock(return_value=ApplicationGeneratedResponse()),
        )
        self.bot = TelegramBot(
            logging.getLogger("test"),
            "123456:dummy-token",
            self.engine,
            discussion=self.service,
            chat_id=42,
        )
        self.real_bot = self.bot._bot
        self.bot._bot = SimpleNamespace(send_message=AsyncMock())
        self.bot._clear_reply_markup = AsyncMock()

    async def asyncTearDown(self):
        await self.real_bot.session.close()

    def message(self, text, user_id=42):
        return Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            text=text,
            chat=Chat(id=42, type="private"),
            from_user=User(id=user_id, is_bot=False, first_name="User"),
        )

    def callback(self, data, user_id=42):
        callback = CallbackQuery(
            id="callback",
            chat_instance="chat",
            from_user=User(id=user_id, is_bot=False, first_name="User"),
            message=self.message("Card"),
            data=data,
        )
        return callback

    async def call_action(self, data, user_id=42):
        # Callback acknowledgements are Telegram calls; mock the method, not the flow.
        from unittest.mock import patch

        with patch.object(CallbackQuery, "answer", new_callable=AsyncMock):
            await self.bot.handle_job_callback(self.callback(data, user_id))

    async def test_forward_flow_and_confirmed_generation_context(self):
        match_id = self.job.match.id
        await self.call_action(f"/discuss:{match_id}")
        await self.bot.handle_text(self.message("/use I have three years of Python."))
        await self.bot.handle_text(self.message("/material profile.projects.project.python"))
        await self.bot.handle_text(self.message("This is only a discussion question."))
        self.engine.append_question.assert_not_awaited()
        await self.call_action(f"/ignore:{match_id}")
        self.engine.update_pipeline_status.assert_not_awaited()
        await self.call_action(f"/finish:{match_id}")
        self.assertNotIn(42, self.bot.active_discussion)
        await self.call_action(f"/apply:{match_id}")
        await self.call_action(f"/gen:letter:{match_id}")
        materials = self.engine.generate_application_for_match.call_args.kwargs["materials"]
        self.assertEqual(
            [m.text for m in materials], ["I have three years of Python.", "Python experience"]
        )
        await self.call_action(f"/applied:yes:{match_id}")
        self.engine.update_pipeline_status.assert_awaited_once_with(match_id, "APPLIED")
        actions = [m.metadata.get("action") for m in self.repo.messages]
        self.assertIn(f"/applied:yes:{match_id}", actions)

    async def test_other_user_and_stale_finish_cannot_change_flow(self):
        match_id = self.job.match.id
        await self.call_action(f"/discuss:{match_id}", user_id=99)
        self.assertEqual(self.repo.messages, [])
        await self.call_action(f"/discuss:{match_id}")
        await self.call_action(f"/finish:{uuid4()}")
        self.assertEqual(self.bot.active_discussion[42], match_id)

    async def test_existing_flow_without_discussion_additions(self):
        match_id = self.job.match.id
        await self.call_action(f"/apply:{match_id}")
        await self.call_action(f"/gen:letter:{match_id}")
        self.engine.generate_application_for_match.assert_awaited_once_with(
            RequestedGeneration(cover_letter=True, questions=False),
            self.job,
        )
        self.assertEqual(self.repo.messages, [])
        await self.call_action(f"/ignore:{match_id}")
        self.engine.update_pipeline_status.assert_awaited_once_with(match_id, "IGNORED")

    async def test_question_input_is_preserved_without_discussion(self):
        from unittest.mock import patch

        match_id = str(self.job.match.id)
        self.bot.active_question_prompt[42] = {"match_id": match_id, "gen_type": "both", "count": 0}
        with patch.object(Message, "reply", new_callable=AsyncMock):
            await self.bot.handle_text(self.message("Why this company?"))
        self.engine.append_question.assert_awaited_once_with(self.job.match.id, "Why this company?")
        self.assertEqual(self.bot.active_question_prompt[42]["count"], 1)

    async def test_questions_advance_with_done_only_and_preserve_generation_type(self):
        match_id = self.job.match.id
        await self.call_action(f"/gen:both:{match_id}")
        prompt = self.bot._bot.send_message.call_args.kwargs
        self.assertEqual(
            [b.text for row in prompt["reply_markup"].inline_keyboard for b in row], ["Done"]
        )
        with patch.object(Message, "reply", new_callable=AsyncMock) as reply:
            await self.bot.handle_text(self.message("Why this company?"))
            await self.bot.handle_text(self.message("What have you built?"))
        self.assertEqual(self.engine.append_question.await_count, 2)
        self.assertIn("question #3", reply.call_args.kwargs["text"])
        self.assertEqual(
            [b.text for row in reply.call_args.kwargs["reply_markup"].inline_keyboard for b in row],
            ["Done"],
        )
        await self.call_action(f"/gen_q:done:{uuid4()}")
        self.engine.generate_application_for_match.assert_not_awaited()
        await self.call_action(f"/gen_q:done:{match_id}")
        self.engine.generate_application_for_match.assert_awaited_once_with(
            RequestedGeneration(cover_letter=True, questions=True), self.job
        )
        await self.call_action(f"/gen_q:done:{match_id}")
        self.assertEqual(self.engine.generate_application_for_match.await_count, 1)

    async def test_matches_command_and_keyboard_query_on_demand(self):
        self.engine.get_matched_job.return_value = self.job
        for text in ("/matches", "🔎 Matches"):
            await self.bot.handle_text(self.message(text))
        self.assertEqual(self.engine.get_matching_count.await_count, 2)
        self.assertEqual(self.engine.get_matched_job.await_count, 2)
        self.assertEqual(self.bot.current_match[42], self.job.match.id)
        self.assertIn("SUITABLE: 2", self.bot._bot.send_message.call_args_list[0].args[1])
        self.engine.get_matched_job.return_value = None
        await self.bot.handle_text(self.message("/matches"))
        self.assertNotIn(42, self.bot.current_match)

    async def test_report_saves_once_without_ai_or_advancing(self):
        self.bot.current_match[42] = self.job.match.id
        await self.bot.handle_text(self.message("✍️ Report"))
        self.assertIn("What do you feel is wrong", self.bot._bot.send_message.call_args.args[1])
        self.engine.report_match.assert_not_awaited()
        await self.bot.handle_text(self.message("This role requires local residency."))
        self.engine.report_match.assert_awaited_once_with(
            self.job.match.id, "This role requires local residency."
        )
        self.assertNotIn(42, self.bot.active_report)
        self.assertEqual(self.bot.current_match[42], self.job.match.id)
        self.engine.get_matched_job.assert_not_awaited()
        self.engine.update_pipeline_status.assert_not_awaited()
        self.service.model.reply.assert_not_awaited()
        self.assertEqual(self.repo.messages, [])

    async def test_report_requires_current_job_and_blocks_navigation(self):
        await self.bot.handle_text(self.message("/report"))
        self.assertNotIn(42, self.bot.active_report)
        self.bot.current_match[42] = self.job.match.id
        await self.bot.handle_text(self.message("/report"))
        for text in ("/matches", "/menu", "/report", "✓ Finish discussion", "/unknown", " "):
            await self.bot.handle_text(self.message(text))
        await self.call_action(f"/ignore:{self.job.match.id}")
        self.engine.get_matching_count.assert_not_awaited()
        self.engine.update_pipeline_status.assert_not_awaited()
        self.engine.report_match.assert_not_awaited()
        await self.bot.handle_text(self.message("/cancel"))
        self.assertNotIn(42, self.bot.active_report)

    async def test_report_failure_keeps_pending_and_retry_can_save(self):
        self.bot.current_match[42] = self.job.match.id
        await self.bot.handle_text(self.message("/report"))
        self.engine.report_match.side_effect = RuntimeError("DB unavailable")
        self.bot.logger = Mock()
        await self.bot.handle_text(self.message("Wrong tier"))
        self.assertIn(42, self.bot.active_report)
        self.engine.report_match.side_effect = None
        await self.bot.handle_text(self.message("Wrong tier"))
        self.assertNotIn(42, self.bot.active_report)
        self.assertEqual(self.engine.report_match.await_count, 2)

    async def test_stale_report_and_cancel_buttons_do_not_change_current_report(self):
        self.bot.current_match[42] = self.job.match.id
        await self.call_action(f"/report:{uuid4()}")
        self.assertNotIn(42, self.bot.active_report)
        await self.call_action(f"/report:{self.job.match.id}")
        _, first_token = self.bot.active_report[42]
        await self.call_action(f"/report_cancel:{first_token}")
        self.assertNotIn(42, self.bot.active_report)
        await self.bot.handle_text(self.message("/report"))
        await self.call_action(f"/report_cancel:{first_token}")
        self.assertIn(42, self.bot.active_report)
        self.engine.report_match.assert_not_awaited()

    async def test_report_during_discussion_preserves_pending_material_input(self):
        match_id = self.job.match.id
        self.bot.current_match[42] = match_id
        await self.call_action(f"/discuss:{match_id}")
        self.bot.discussion_input[42] = "/use"
        self.service.send = AsyncMock()
        await self.bot.handle_text(self.message("/report"))
        await self.bot.handle_text(self.message("Technical score too low"))
        self.service.send.assert_not_awaited()
        self.assertEqual(self.bot.active_discussion[42], match_id)
        self.assertEqual(self.bot.discussion_input[42], "/use")
        await self.bot.handle_text(self.message("/report"))
        await self.bot.handle_text(self.message("/cancel"))
        self.assertEqual(self.bot.discussion_input[42], "/use")
        await self.bot.handle_text(self.message("I built distributed systems"))
        self.assertTrue(self.service.send.call_args.kwargs["confirmed"])
        self.assertEqual(self.engine.report_match.await_count, 1)

    async def test_report_during_questions_resumes_question_entry(self):
        match_id = self.job.match.id
        self.bot.current_match[42] = match_id
        state = {"match_id": str(match_id), "gen_type": "questions", "count": 0}
        self.bot.active_question_prompt[42] = state.copy()
        await self.bot.handle_text(self.message("/report"))
        await self.bot.handle_text(self.message("Wrong tier"))
        self.engine.append_question.assert_not_awaited()
        self.assertEqual(self.bot.active_question_prompt[42], state)
        with patch.object(Message, "reply", new_callable=AsyncMock):
            await self.bot.handle_text(self.message("Why this company?"))
        self.engine.append_question.assert_awaited_once_with(match_id, "Why this company?")

    async def test_report_cannot_save_if_current_match_changed(self):
        self.bot.current_match[42] = self.job.match.id
        await self.bot.handle_text(self.message("/report"))
        self.bot.current_match[42] = uuid4()
        await self.bot.handle_text(self.message("Wrong tier"))
        self.engine.report_match.assert_not_awaited()
        self.assertNotIn(42, self.bot.active_report)

    async def test_other_user_cannot_report_or_cancel(self):
        self.bot.current_match[42] = self.job.match.id
        await self.bot.handle_text(self.message("/report", user_id=99))
        await self.call_action(f"/report:{self.job.match.id}", user_id=99)
        self.assertNotIn(42, self.bot.active_report)
        await self.bot.handle_text(self.message("/report"))
        _, token = self.bot.active_report[42]
        await self.call_action(f"/report_cancel:{token}", user_id=99)
        self.assertIn(42, self.bot.active_report)
        self.engine.report_match.assert_not_awaited()

    async def test_report_after_discussion_and_cancelled_edit_preserves_saved_report(self):
        match_id = self.job.match.id
        self.bot.current_match[42] = match_id
        await self.call_action(f"/discuss:{match_id}")
        await self.call_action(f"/finish:{match_id}")
        await self.call_action(f"/report:{match_id}")
        await self.bot.handle_text(self.message("Wrong strategic assessment"))
        await self.bot.handle_text(self.message("/report"))
        await self.bot.handle_text(self.message("/cancel"))
        self.engine.report_match.assert_awaited_once_with(match_id, "Wrong strategic assessment")
        self.assertNotIn(42, self.bot.active_discussion)
        self.assertEqual(self.bot.current_match[42], match_id)
        await self.bot.handle_text(self.message("/report"))
        await self.bot.handle_text(self.message("Updated feedback"))
        self.engine.report_match.assert_awaited_with(match_id, "Updated feedback")

    async def test_completed_match_is_not_reportable_when_next_card_fails(self):
        match_id = self.job.match.id
        self.bot.current_match[42] = match_id
        self.engine.get_matched_job.side_effect = RuntimeError("DB unavailable")
        self.bot.logger = Mock()
        await self.call_action(f"/applied:yes:{match_id}")
        self.assertNotIn(42, self.bot.current_match)
        await self.bot.handle_text(self.message("/report"))
        self.assertNotIn(42, self.bot.active_report)
        self.engine.report_match.assert_not_awaited()

    async def test_discussion_menu_has_report_button(self):
        self.bot.active_discussion[42] = self.job.match.id
        await self.bot.handle_text(self.message("🏠 Menu"))
        markup = self.bot._bot.send_message.call_args.kwargs["reply_markup"]
        self.assertIn("✍️ Report", [b.text for row in markup.keyboard for b in row])

    async def test_startup_shows_first_match_without_counts_or_monitor(self):
        self.engine.get_matched_job.return_value = self.job
        self.bot._bot.delete_webhook = AsyncMock()
        self.bot._bot.set_my_commands = AsyncMock()
        self.bot._bot.session = SimpleNamespace(close=AsyncMock())
        self.bot._dp.start_polling = AsyncMock()
        await self.bot.run()
        self.engine.get_matched_job.assert_awaited_once()
        self.engine.get_matching_count.assert_not_awaited()
        self.bot._dp.start_polling.assert_awaited_once()
        self.bot._bot.session.close.assert_awaited_once()
        commands = self.bot._bot.set_my_commands.call_args.args[0]
        self.assertEqual([c.command for c in commands], ["matches", "report", "menu", "cancel"])

    async def test_startup_match_failure_still_starts_polling(self):
        self.engine.get_matched_job.side_effect = RuntimeError("Database unavailable")
        self.bot.logger = Mock()
        self.bot._bot.delete_webhook = AsyncMock()
        self.bot._bot.set_my_commands = AsyncMock()
        self.bot._bot.session = SimpleNamespace(close=AsyncMock())
        self.bot._dp.start_polling = AsyncMock()
        await self.bot.run()
        self.bot._dp.start_polling.assert_awaited_once()
        self.bot.logger.exception.assert_called_once()

    async def test_discussion_defaults_to_chat_and_menu_buttons_prompt_for_input(self):
        match_id = self.job.match.id
        await self.call_action(f"/discuss:{match_id}")
        self.service.send = AsyncMock(wraps=self.service.send)
        await self.bot.handle_text(self.message("Does this role suit me?"))
        self.assertEqual(self.service.send.call_args.args[3], "Does this role suit me?")
        self.assertFalse(self.service.send.call_args.kwargs["confirmed"])
        await self.bot.handle_text(self.message("✅ Use in application"))
        self.assertEqual(self.service.send.await_count, 1)
        await self.bot.handle_text(self.message("I have worked on distributed systems."))
        self.assertTrue(self.service.send.call_args.kwargs["confirmed"])
        await self.bot.handle_text(self.message("📎 Context"))
        await self.bot.handle_text(self.message("I prefer remote roles."))
        self.assertEqual(
            self.service.send.call_args.args[3],
            "Attached context for discussion:\nI prefer remote roles.",
        )
        self.assertFalse(self.service.send.call_args.kwargs["confirmed"])
        await self.bot.handle_text(self.message("Another chat message"))
        self.assertEqual(self.service.send.call_args.args[3], "Another chat message")
        self.assertFalse(self.service.send.call_args.kwargs["confirmed"])
        self.engine.append_question.assert_not_awaited()

    async def test_discussion_material_buttons_cancel_and_restore_main_menu(self):
        match_id = self.job.match.id
        await self.call_action(f"/discuss:{match_id}")
        self.service.send = AsyncMock(wraps=self.service.send)
        await self.bot.handle_text(self.message("📚 Materials"))
        self.service.send.assert_not_awaited()
        self.assertIn(
            "profile.projects.project.python", self.bot._bot.send_message.call_args.kwargs["text"]
        )
        await self.bot.handle_text(self.message("➕ Include material"))
        await self.bot.handle_text(self.message("profile.projects.project.python"))
        self.assertEqual(
            self.service.send.call_args.kwargs["material_path"], "profile.projects.project.python"
        )
        self.assertTrue(self.service.send.call_args.kwargs["confirmed"])
        await self.bot.handle_text(self.message("✅ Use in application"))
        await self.bot.handle_text(self.message("/cancel"))
        await self.bot.handle_text(self.message("Just chatting again"))
        self.assertFalse(self.service.send.call_args.kwargs["confirmed"])
        await self.bot.handle_text(self.message("📎 Context"))
        await self.bot.handle_text(self.message("✓ Finish discussion"))
        self.assertNotIn(42, self.bot.active_discussion)
        self.assertNotIn(42, self.bot.discussion_input)
        restored_menu = self.bot._bot.send_message.call_args_list[-2].kwargs["reply_markup"]
        self.assertEqual(
            [[b.text for b in row] for row in restored_menu.keyboard],
            [["🔎 Matches", "✍️ Report"], ["🏠 Menu"]],
        )

    async def test_explicit_discussion_command_overrides_pending_button(self):
        await self.call_action(f"/discuss:{self.job.match.id}")
        self.service.send = AsyncMock(wraps=self.service.send)
        await self.bot.handle_text(self.message("✅ Use in application"))
        await self.bot.handle_text(self.message("/context This is context only"))
        self.assertFalse(self.service.send.call_args.kwargs["confirmed"])
        self.assertNotIn(42, self.bot.discussion_input)
        await self.bot.handle_text(self.message("Ordinary message"))
        self.assertEqual(self.service.send.call_args.args[3], "Ordinary message")


if __name__ == "__main__":
    unittest.main()
