import asyncio
import contextlib
import json
import os
import uuid
from logging import Logger
from typing import Any

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from src.analytics.discussion import DiscussionService
from src.analytics.protocols import DataIntegrityException, MatchingProvider
from src.shared import SuitabilityTier
from src.shared.models import RequestedGeneration

CHAT_ID = 975840884


class TelegramBot:
    def __init__(
        self,
        logger: Logger,
        token: str,
        engine: MatchingProvider,
        discussion: DiscussionService | None = None,
        chat_id: int = CHAT_ID,
    ) -> None:
        self.logger = logger
        self._bot = Bot(token=token)
        self._dp = Dispatcher()
        self.matching_engine = engine
        self.discussion = discussion
        self.chat_id = chat_id
        self.active_discussion: dict[int, uuid.UUID] = {}
        self._chat_locks: dict[int, asyncio.Lock] = {}

        self.questions: dict[str, list[str]] = dict()
        self.active_question_prompt: dict[int, dict[str, Any]] = dict()
        self._register_handlers()

    def _register_handlers(self) -> None:
        self._dp.message.register(self.cmd_start, CommandStart())
        self._dp.message.register(self.handle_text, F.text)
        self._dp.callback_query.register(self.handle_job_callback)

    def _chat_lock(self, chat_id: int) -> asyncio.Lock:
        return self._chat_locks.setdefault(chat_id, asyncio.Lock())

    async def handle_job_callback(self, callback: CallbackQuery) -> None:
        if not isinstance(callback.message, Message) or not callback.data:
            return
        chat_id = callback.message.chat.id
        if chat_id != self.chat_id or callback.from_user.id != self.chat_id:
            await callback.answer(
                "This bot is configured for another private chat.", show_alert=True
            )
            return
        handlers = {
            "/next": self.handle_next,
            "/apply": self.handle_apply,
            "/ignore": self.handle_ignore,
            "/gen_q:next": self.handle_question_next,
            "/gen_q:done": self.handle_question_done,
            "/gen": self.handle_generate,
            "/applied": self.handle_did_apply,
            "/discuss": self.handle_discuss,
            "/finish": self.handle_discussion_finish,
        }
        prefix = callback.data.rsplit(":", 1)[0]
        if prefix.startswith("/applied:") or prefix.startswith("/gen:"):
            prefix = prefix.split(":")[0]
        handler = handlers.get(prefix)
        if handler is None:
            return
        if self._chat_lock(chat_id).locked():
            await callback.answer("Please wait for the current request to finish.")
            return
        async with self._chat_lock(chat_id):
            if chat_id in self.active_discussion and prefix != "/finish":
                await callback.answer("Finish the current discussion first.", show_alert=True)
                return
            try:
                await handler(callback)
                if (
                    self.discussion
                    and ":" in callback.data
                    and prefix not in {"/discuss", "/finish"}
                ):
                    await self.discussion.record_action(
                        uuid.UUID(callback.data.rsplit(":", 1)[1]),
                        callback.from_user.id,
                        chat_id,
                        callback.data,
                    )
            except ValueError as error:
                await self._bot.send_message(chat_id, str(error))
            except Exception:
                self.logger.exception("Job action failed")
                await self._bot.send_message(
                    chat_id, "Could not complete this action. Please try again."
                )

    async def handle_discuss(self, callback: CallbackQuery) -> None:
        await callback.answer()
        if (
            self.discussion is None
            or not callback.data
            or not isinstance(callback.message, Message)
        ):
            return
        chat_id = callback.message.chat.id
        if chat_id in self.active_question_prompt:
            raise ValueError("Finish entering application questions first.")
        match_id = uuid.UUID(callback.data.split(":")[1])
        job = await self.matching_engine.get_job_by_match_id(match_id)
        if job is None:
            raise ValueError("Job not found.")
        await self._bot.send_message(chat_id, "Selecting relevant profile materials with Jev...")
        await self.discussion.start(job, callback.from_user.id, chat_id)
        self.active_discussion[chat_id] = match_id
        await self._clear_reply_markup(callback.message)
        await self._bot.send_message(
            chat_id,
            "Discussion started with this job, Jev-selected materials and its assessment. "
            "What would you like to clarify?\n\n"
            "/context <text> — attach information for discussion\n"
            "/use <text> — confirm a correction or experience detail for application generation\n"
            "/materials [filter] — list available profile paths\n"
            "/material <path> — include that profile block in the application",
            reply_markup=self._discussion_markup(match_id),
        )

    def _discussion_markup(self, match_id: uuid.UUID) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Finish discussion", callback_data=f"/finish:{match_id}"
                    ),
                ]
            ]
        )

    async def handle_discussion_finish(self, callback: CallbackQuery) -> None:
        await callback.answer()
        if (
            self.discussion is None
            or not callback.data
            or not isinstance(callback.message, Message)
        ):
            return
        chat_id = callback.message.chat.id
        match_id = uuid.UUID(callback.data.split(":")[1])
        if self.active_discussion.get(chat_id) != match_id:
            raise ValueError("This discussion is no longer active.")
        await self.discussion.finish(match_id, callback.from_user.id, chat_id)
        del self.active_discussion[chat_id]
        await self._clear_reply_markup(callback.message)
        await self._bot.send_message(
            chat_id,
            "Discussion finished. Confirmed additions will be used when generating materials.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Prepare application", callback_data=f"/apply:{match_id}"
                        ),
                        InlineKeyboardButton(text="Ignore", callback_data=f"/ignore:{match_id}"),
                    ]
                ]
            ),
        )

    async def handle_text(self, message: Message) -> None:
        if (
            message.chat.id != self.chat_id
            or not message.from_user
            or message.from_user.id != self.chat_id
        ):
            return
        async with self._chat_lock(message.chat.id):
            match_id = self.active_discussion.get(message.chat.id)
            if match_id is None or self.discussion is None:
                if message.text and not message.text.startswith("/"):
                    await self.handle_question_input(message)
                return
            text = (message.text or "").strip()
            parts = text.split(maxsplit=1)
            command = parts[0] if parts else ""
            argument = parts[1] if len(parts) > 1 else ""
            confirmed = command in {"/use", "/material"}
            material_path = None
            if command == "/materials":
                paths = [
                    m.id
                    for m in self.discussion.profile.get_materials()
                    if argument.casefold() in m.id.casefold()
                ]
                await self._safe_send_message(
                    message.chat.id,
                    "\n".join(paths[:40]) or "No matching profile paths.",
                    reply_markup=self._discussion_markup(match_id),
                )
                return
            if command == "/material":
                try:
                    material = self.discussion.profile.get_material(argument.strip())
                except DataIntegrityException:
                    await message.reply(
                        "Unknown profile path. Use /materials [filter] to find one."
                    )
                    return
                text, material_path = material.text, material.id
            elif command in {"/use", "/context"}:
                text = argument.strip()
                if not text:
                    await message.reply(f"Usage: {command} <text>")
                    return
                if command == "/context":
                    text = "Attached context for discussion:\n" + text
            elif command.startswith("/"):
                await message.reply(
                    "Use /context, /use, /materials or /material during discussion."
                )
                return
            try:
                answer = await self.discussion.send(
                    match_id,
                    message.from_user.id,
                    message.chat.id,
                    text,
                    confirmed=confirmed,
                    material_path=material_path,
                )
                await self._safe_send_message(
                    message.chat.id,
                    ("Saved for application generation.\n\n" if confirmed else "") + answer,
                    reply_markup=self._discussion_markup(match_id),
                )
            except ValueError as error:
                await message.reply(str(error), reply_markup=self._discussion_markup(match_id))
            except Exception:
                self.logger.exception("Discussion request failed for match %s", match_id)
                await message.reply(
                    "Could not get an AI response. Please try again.",
                    reply_markup=self._discussion_markup(match_id),
                )

    async def cmd_start(self, message: Message) -> None:
        user_id = message.from_user.id if message.from_user else "unknown"
        self.logger.info(f"Start command received from user {user_id}, chat {message.chat.id}")
        await message.answer(
            text="""
                Hi, its Dorker - open-source engine that handles scraping and application.
                """,
            reply_markup=self._get_start_markup(),
        )

    async def handle_next(self, callback: CallbackQuery) -> None:
        await callback.answer()
        await self._show_next_job()

    async def _show_next_job(self) -> None:
        job = await self.matching_engine.get_matched_job(
            [SuitabilityTier.RUNWAY, SuitabilityTier.SUITABLE, SuitabilityTier.STRETCH], offset=0
        )
        if not job:
            await self._bot.send_message(
                chat_id=self.chat_id,
                text="No more pending matched jobs found.",
            )
            return

        salary = "unspecified"
        if job.salary_min is not None and job.salary_max is not None:
            salary = f"{job.salary_currency}{job.salary_min}-{job.salary_max}"

        tier_val = (
            job.match.suitability_tier.value
            if job.match and job.match.suitability_tier
            else "unknown"
        )
        summary = job.match.job_summary if job.match and job.match.job_summary else ""
        formatted_summary = summary
        if summary.strip().startswith("{"):
            with contextlib.suppress(Exception):
                parsed = json.loads(summary)
                if isinstance(parsed, dict) and "summary" in parsed:
                    formatted_summary = (
                        f"📝 {parsed.get('summary', '')}\n\n"
                        f"👍 Pros:\n{parsed.get('pros', '')}\n\n"
                        f"⚠️ Cons:\n{parsed.get('cons', '')}"
                    )

        match_id = job.match.id if job.match else ""

        text = (
            f"Title: {job.title}\n"
            f"Company: {job.company or 'Unknown'}\n"
            f"Location: {job.location}\n"
            f"Salary: {salary}\n"
            f"URL: {job.url}\n\n"
            f"Tier: {tier_val}\n\n"
            f"{formatted_summary}"
        )

        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Ignore", callback_data=f"/ignore:{match_id}"),
                    InlineKeyboardButton(text="Apply", callback_data=f"/apply:{match_id}"),
                ],
                [
                    InlineKeyboardButton(
                        text="Applied with CV only", callback_data=f"/applied:yes:{match_id}"
                    ),
                ],
            ]
        )
        if self.discussion is not None:
            markup.inline_keyboard.append(
                [
                    InlineKeyboardButton(
                        text="Discuss with AI", callback_data=f"/discuss:{match_id}"
                    ),
                ]
            )
        await self._bot.send_message(
            chat_id=self.chat_id,
            text=text,
            reply_markup=markup,
        )

    async def _clear_reply_markup(self, message: Any) -> None:
        if isinstance(message, Message):
            with contextlib.suppress(TelegramBadRequest):
                await message.edit_reply_markup(reply_markup=None)

    async def handle_apply(self, callback: CallbackQuery) -> None:
        await callback.answer()
        if not callback.data or not isinstance(callback.message, Message):
            return
        match_id = callback.data.split(":")[1]

        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Questions", callback_data=f"/gen:questions:{match_id}"
                    ),
                    InlineKeyboardButton(
                        text="Cover Letter", callback_data=f"/gen:letter:{match_id}"
                    ),
                    InlineKeyboardButton(text="Both", callback_data=f"/gen:both:{match_id}"),
                ],
                [InlineKeyboardButton(text="Ignore", callback_data=f"/ignore:{match_id}")],
            ]
        )

        await self._clear_reply_markup(callback.message)
        await self._bot.send_message(
            chat_id=self.chat_id, text="What would you like to generate?", reply_markup=markup
        )

    async def handle_ignore(self, callback: CallbackQuery) -> None:
        await callback.answer()
        if not callback.data or not isinstance(callback.message, Message):
            return
        match_id = callback.data.split(":")[1]
        self.active_question_prompt.pop(callback.message.chat.id, None)

        await self.matching_engine.update_pipeline_status(uuid.UUID(match_id), "IGNORED")
        await self._clear_reply_markup(callback.message)
        await self._show_next_job()

    async def _safe_send_message(self, chat_id: int, text: str, **kwargs: Any) -> None:
        if not text:
            return
        chunk_size = 4000
        markup = kwargs.pop("reply_markup", None)
        for i in range(0, len(text), chunk_size):
            await self._bot.send_message(
                chat_id=chat_id,
                text=text[i : i + chunk_size],
                reply_markup=markup if i + chunk_size >= len(text) else None,
                **kwargs,
            )

    async def handle_generate(self, callback: CallbackQuery) -> None:
        await callback.answer()
        if not callback.data or not isinstance(callback.message, Message):
            return
        await self._clear_reply_markup(callback.message)

        _, gen_type, match_id = callback.data.split(":")
        chat_id = callback.message.chat.id

        if gen_type == "letter":
            await self._execute_generation(chat_id=chat_id, match_id=match_id, gen_type=gen_type)
        else:
            self.active_question_prompt[chat_id] = {
                "match_id": match_id,
                "gen_type": gen_type,
                "count": 0,
            }
            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="Next", callback_data=f"/gen_q:next:{match_id}"),
                        InlineKeyboardButton(text="Done", callback_data=f"/gen_q:done:{match_id}"),
                    ]
                ]
            )
            await self._bot.send_message(
                chat_id=chat_id,
                text=(
                    "📝 Please enter question #1 for this application as a text message.\n\n"
                    "You can click Next after each question to continue, or Done when finished."
                ),
                reply_markup=markup,
            )

    async def handle_question_input(self, message: Message) -> None:
        chat_id = message.chat.id
        if chat_id not in self.active_question_prompt or not message.text:
            return

        state = self.active_question_prompt[chat_id]
        match_id = state["match_id"]
        question_text = message.text.strip()
        if not question_text:
            return

        try:
            await self.matching_engine.append_question(uuid.UUID(match_id), question_text)
            self.questions.setdefault(match_id, []).append(question_text)
            state["count"] += 1
            current_count = state["count"]

            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="Next", callback_data=f"/gen_q:next:{match_id}"),
                        InlineKeyboardButton(text="Done", callback_data=f"/gen_q:done:{match_id}"),
                    ]
                ]
            )
            await message.reply(
                text=(
                    f"✅ Saved question #{current_count}:\n"
                    f"_{question_text}_\n\n"
                    f"Send question #{current_count + 1} as a message, click Next to prompt next, or click Done to begin generation."
                ),
                reply_markup=markup,
            )
        except Exception as e:
            self.logger.exception(f"Failed to append question for match {match_id}: {e}")
            await message.reply(f"⚠️ Failed to save question: {e}")

    async def handle_question_next(self, callback: CallbackQuery) -> None:
        await callback.answer()
        if not callback.data or not isinstance(callback.message, Message):
            return
        chat_id = callback.message.chat.id
        if chat_id not in self.active_question_prompt:
            await callback.answer("No active question prompt.", show_alert=True)
            return

        state = self.active_question_prompt[chat_id]
        match_id = state["match_id"]
        next_num = state["count"] + 1

        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Done", callback_data=f"/gen_q:done:{match_id}")]
            ]
        )
        await self._bot.send_message(
            chat_id=chat_id,
            text=f"📝 Ready for question #{next_num}. Please send the question as a text message, or click Done:",
            reply_markup=markup,
        )

    async def handle_question_done(self, callback: CallbackQuery) -> None:
        await callback.answer()
        if not callback.data or not isinstance(callback.message, Message):
            return
        chat_id = callback.message.chat.id
        await self._clear_reply_markup(callback.message)

        match_id = callback.data.split(":")[2]
        gen_type = "questions"
        if chat_id in self.active_question_prompt:
            gen_type = self.active_question_prompt[chat_id].get("gen_type", "questions")
            del self.active_question_prompt[chat_id]

        await self._execute_generation(chat_id=chat_id, match_id=match_id, gen_type=gen_type)

    async def _execute_generation(self, chat_id: int, match_id: str, gen_type: str) -> None:
        await self._bot.send_message(chat_id=chat_id, text="⏳ Generating application materials...")

        job = await self.matching_engine.get_job_by_match_id(uuid.UUID(match_id))
        if not job:
            await self._bot.send_message(chat_id=chat_id, text="Job not found.")
            return

        request = RequestedGeneration(
            cover_letter=(gen_type in ["both", "letter"]),
            questions=(gen_type in ["both", "questions"]),
        )

        try:
            materials = (
                await self.discussion.confirmed_materials(
                    uuid.UUID(match_id),
                    self.chat_id,
                    chat_id,
                )
                if self.discussion
                else []
            )
            selected_packet = (
                await self.discussion.selected_packet(
                    uuid.UUID(match_id),
                    self.chat_id,
                    chat_id,
                )
                if self.discussion
                else None
            )
            if selected_packet is not None:
                resp = await self.matching_engine.generate_application_for_match(
                    request,
                    job,
                    materials=materials,
                    selected_packet=selected_packet,
                )
            elif materials:
                resp = await self.matching_engine.generate_application_for_match(
                    request,
                    job,
                    materials=materials,
                )
            else:
                resp = await self.matching_engine.generate_application_for_match(request, job)
        except Exception as e:
            self.logger.exception(f"Generation failed for match {match_id}: {e}")
            await self._bot.send_message(chat_id=chat_id, text=f"⚠️ Generation failed: {e}")
            return

        if resp.cover_letter:
            await self._send_cover_letter(chat_id, job, resp.cover_letter)

        if resp.questions:
            await self._send_questions(chat_id, resp.questions)

        if not resp.cover_letter and not resp.questions:
            await self._bot.send_message(
                chat_id=chat_id, text="⚠️ Generation completed, but no materials were returned."
            )

        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Yes", callback_data=f"/applied:yes:{match_id}"),
                    InlineKeyboardButton(text="No", callback_data=f"/applied:no:{match_id}"),
                ]
            ]
        )
        await self._bot.send_message(chat_id=chat_id, text="Did you apply?", reply_markup=markup)

    async def _send_cover_letter(self, chat_id: int, job: Any, cover_letter: str) -> None:
        from src.shared.scripts.cover_letter_file_create import generate_cover_letter

        company_name = job.company or "Company"
        safe_name = "".join(c for c in company_name if c.isalnum() or c in ("-", "_")) or "Company"
        pdf_path = os.path.join("/tmp", f"CoverLetter_{safe_name}.pdf")

        data = {
            "name": "Serafym Podolyanchuk",
            "city": "Kyiv",
            "phone": "+380-(96)-563-80-50",
            "email": "podolancukserafim@gmail.com",
            "linkedin": "serafym-podolyanchuk",
            "github": "Developer-dreamer",
            "recipient_title": "Hiring Manager",
            "company_name": company_name,
            "company_location": job.location or "Location",
            "salutation_name": "Hiring Manager",
            "letter_paragraphs": cover_letter.split("\n\n") if cover_letter else [],
        }

        template_path = os.path.join(
            os.getcwd(), "artifacts/data/templates/cover_letter_template.tex"
        )

        try:
            await asyncio.to_thread(generate_cover_letter, data, template_path, pdf_path)
            pdf_file = FSInputFile(pdf_path)
            await self._bot.send_document(
                chat_id=chat_id, document=pdf_file, caption="Here is your cover letter."
            )
        except FileNotFoundError as e:
            self.logger.warning(f"LaTeX engine not installed: {e}")
            await self._bot.send_message(
                chat_id=chat_id,
                text="⚠️ *PDF compilation skipped* ('xelatex' not installed on system).",
            )
            await self._safe_send_message(
                chat_id=chat_id,
                text=f"📄 *Cover Letter:*\n\n{cover_letter}",
            )
        except Exception as e:
            self.logger.exception(f"Failed to generate cover letter PDF: {e}")
            err_str = str(e).strip()
            first_err_line = err_str.splitlines()[-1] if err_str else "Unknown error"
            await self._bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ Failed to compile PDF ({first_err_line[:120]}). Falling back to text:",
            )
            await self._safe_send_message(
                chat_id=chat_id,
                text=f"📄 *Cover Letter:*\n\n{cover_letter}",
            )

    async def _send_questions(self, chat_id: int, questions: list[Any]) -> None:
        if not questions:
            return
        lines = ["📋 *Questions & Answers:*\n"]
        for idx, q in enumerate(questions, start=1):
            ans = q.answer or "N/A"
            lines.append(f"❓ *{idx}. {q.question}*\n💡 {ans}\n")
        await self._safe_send_message(chat_id=chat_id, text="\n".join(lines))

    async def handle_did_apply(self, callback: CallbackQuery) -> None:
        await callback.answer()
        if not callback.data or not isinstance(callback.message, Message):
            return
        _, answer, match_id = callback.data.split(":")
        self.active_question_prompt.pop(callback.message.chat.id, None)

        status = "APPLIED" if answer == "yes" else "DECLINED"
        await self.matching_engine.update_pipeline_status(uuid.UUID(match_id), status)

        await self._clear_reply_markup(callback.message)
        await self._show_next_job()

    async def run(self) -> None:
        self.logger.info("Starting bot polling...")
        await self._bot.delete_webhook(drop_pending_updates=True)

        monitor_task = asyncio.create_task(self._monitor_matches())
        polling_task = asyncio.create_task(self._dp.start_polling(self._bot))

        try:
            # Wait until polling stops (e.g. via SIGINT intercepted by aiogram)
            await polling_task
        finally:
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass
            await self.stop()

    async def _monitor_matches(self) -> None:
        try:
            while True:
                res = await self.matching_engine.get_matching_count()
                await self._bot.send_message(
                    chat_id=self.chat_id,
                    text=(
                        f"Your matches are ready:\n"
                        f"SUITABLE: {res.get('SUITABLE', 0)}\n"
                        f"STRETCH: {res.get('STRETCH', 0)}\n"
                        f"RUNWAY: {res.get('RUNWAY', 0)}"
                    ),
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="Show?",
                                    callback_data="/next",
                                ),
                            ]
                        ]
                    ),
                )
                await asyncio.sleep(60000)
        except asyncio.CancelledError:
            self.logger.info("Match monitor background task cancelled.")
            raise

    async def stop(self) -> None:
        await self._bot.session.close()

    def _get_start_markup(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Check the repository!",
                        url="https://github.com/Developer-dreamer/dorker",
                    ),
                ]
            ]
        )
