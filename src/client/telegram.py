import asyncio
import contextlib
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

from src.analytics.protocols import MatchingProvider
from src.shared import SuitabilityTier

CHAT_ID = 975840884


class TelegramBot:
    def __init__(self, logger: Logger, token: str, engine: MatchingProvider) -> None:
        self.logger = logger
        self._bot = Bot(token=token)
        self._dp = Dispatcher()
        self._register_handlers()
        self.matching_engine = engine

    def _register_handlers(self) -> None:
        self._dp.message.register(self.cmd_start, CommandStart())
        self._dp.callback_query.register(self.handle_next, F.data == "/next")

        self._dp.callback_query.register(self.handle_apply, F.data.startswith("/apply:"))
        self._dp.callback_query.register(self.handle_ignore, F.data.startswith("/ignore:"))
        self._dp.callback_query.register(self.handle_generate, F.data.startswith("/gen:"))
        self._dp.callback_query.register(self.handle_did_apply, F.data.startswith("/applied:"))

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
                chat_id=CHAT_ID,
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
        match_id = job.match.id if job.match else ""

        text = (
            f"Title: {job.title}\n"
            f"Company: {job.company or 'Unknown'}\n"
            f"Location: {job.location}\n"
            f"Salary: {salary}\n"
            f"URL: {job.url}\n\n"
            f"Tier: {tier_val}\n"
            f"Summary:\n"
            f"{summary}"
        )

        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Ignore", callback_data=f"/ignore:{match_id}"),
                    InlineKeyboardButton(text="Apply", callback_data=f"/apply:{match_id}"),
                ]
            ]
        )
        await self._bot.send_message(
            chat_id=CHAT_ID,
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
                        text="Follow Up", callback_data=f"/gen:followup:{match_id}"
                    ),
                    InlineKeyboardButton(
                        text="Cover Letter", callback_data=f"/gen:letter:{match_id}"
                    ),
                    InlineKeyboardButton(text="Both", callback_data=f"/gen:both:{match_id}"),
                ]
            ]
        )

        await self._clear_reply_markup(callback.message)
        await self._bot.send_message(
            chat_id=CHAT_ID, text="What would you like to generate?", reply_markup=markup
        )

    async def handle_ignore(self, callback: CallbackQuery) -> None:
        await callback.answer()
        if not callback.data or not isinstance(callback.message, Message):
            return
        match_id = callback.data.split(":")[1]

        await self.matching_engine.update_pipeline_status(uuid.UUID(match_id), "DECLINED")
        await self._clear_reply_markup(callback.message)
        await self._show_next_job()

    async def handle_generate(self, callback: CallbackQuery) -> None:
        await callback.answer("Generating application materials...", show_alert=False)
        if not callback.data or not isinstance(callback.message, Message):
            return
        await self._clear_reply_markup(callback.message)

        _, gen_type, match_id = callback.data.split(":")
        job = await self.matching_engine.get_job_by_match_id(uuid.UUID(match_id))

        if not job:
            await self._bot.send_message(CHAT_ID, "Job not found.")
            return

        resp = await self.matching_engine.generate_application_for_match(job)

        if gen_type in ["both", "letter"]:
            from src.shared.scripts.cover_letter_file_create import generate_cover_letter

            company_name = job.company or "Company"
            safe_name = (
                "".join(c for c in company_name if c.isalnum() or c in ("-", "_")) or "Company"
            )
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
                "letter_paragraphs": resp.cover_letter.split("\n\n") if resp.cover_letter else [],
            }

            template_path = os.path.join(
                os.getcwd(), "artifacts/data/templates/cover_letter_template.tex"
            )

            try:
                # Use asyncio to not block the bot during compilation
                await asyncio.to_thread(generate_cover_letter, data, template_path, pdf_path)
                pdf_file = FSInputFile(pdf_path)
                msg_text = (
                    resp.follow_up_message if gen_type == "both" else "Here is your cover letter."
                )
                await self._bot.send_document(chat_id=CHAT_ID, document=pdf_file, caption=msg_text)
            except FileNotFoundError as e:
                self.logger.warning(f"LaTeX engine not installed: {e}")
                cover_letter_text = (
                    f"⚠️ *PDF compilation skipped* ('xelatex' not installed on system).\n\n"
                    f"*Cover Letter:*\n{resp.cover_letter}"
                )
                if gen_type == "both":
                    cover_letter_text = (
                        f"*Follow Up Message:*\n{resp.follow_up_message}\n\n" + cover_letter_text
                    )
                await self._bot.send_message(
                    chat_id=CHAT_ID,
                    text=cover_letter_text,
                )
            except Exception as e:
                self.logger.exception(f"Failed to generate cover letter PDF: {e}")
                await self._bot.send_message(
                    chat_id=CHAT_ID,
                    text=f"⚠️ Failed to compile PDF ({e}).\n\nCover Letter:\n{resp.cover_letter}\n\nFollow Up:\n{resp.follow_up_message}",
                )
        else:
            await self._bot.send_message(chat_id=CHAT_ID, text=resp.follow_up_message)

        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Yes", callback_data=f"/applied:yes:{match_id}"),
                    InlineKeyboardButton(text="No", callback_data=f"/applied:no:{match_id}"),
                ]
            ]
        )

        await self._bot.send_message(chat_id=CHAT_ID, text="Did you apply?", reply_markup=markup)

    async def handle_did_apply(self, callback: CallbackQuery) -> None:
        await callback.answer()
        if not callback.data or not isinstance(callback.message, Message):
            return
        _, answer, match_id = callback.data.split(":")

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
                    chat_id=CHAT_ID,
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
                await asyncio.sleep(600)
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
