import asyncio
from asyncio import Queue
from typing import Any

from src.config.logger import Logger
from src.domain.model.match import MatchedJob
from src.domain.service.analyst import JobAnalyst
from src.infra.bot.handler import BotHandler


class ApplicationEngine:
    def __init__(self, logger: Logger, telegram_bot: BotHandler,
                 analyst: JobAnalyst, command_queue: Queue[Any]):
        self.logger = logger
        self.telegram_bot = telegram_bot
        self.analyst = analyst

        self.command_queue = command_queue

    async def start(self) -> None:
        self.logger.info("Initializing Application Engine runtime infrastructure...")

        # Execute concurrent worker loops within a single event loop lifecycle
        execution_tasks = [
            asyncio.create_task(self.telegram_bot.start_polling()),
            asyncio.create_task(self._listen_user_commands())
        ]

        await asyncio.gather(*execution_tasks)

    async def _listen_user_commands(self) -> None:
        """
        Asynchronous background worker that processes user intent events 
        emitted from the transport layer. Prevents long-running LLM calls from freezing the bot.
        """
        self.logger.info("User command event loop consumer initialized.")
        while True:
            try:
                event = await self.command_queue.get()
                command = event.get("command")
                chat_id = event.get("chat_id")

                self.logger.info(f"Processing command intent '{command}' for chat_id {chat_id}")

                if command == "FIND_TOP_MATCHES":
                    matches = await self.analyst.find_top_matches()

                    if not matches:
                        await self.telegram_bot.send_direct_message(
                            chat_id=chat_id,
                            text="Evaluation complete. No new high-alignment jobs found matching your criteria."
                        )
                        continue

                    # Compile and push granular match telemetry to the user
                    for (url, match) in matches:
                        report = self.build_report(url, match)
                        await self.telegram_bot.send_direct_message(chat_id=chat_id, text=report)

            except Exception as e:
                self.logger.error(f"Critical execution failure in user command pipeline: {str(e)}")
            finally:
                self.command_queue.task_done()

    def build_report(self, url: str, job: MatchedJob) -> str:
        return (
            f"Matching status: {job.application_status}.\n\n"
            f"Job info: {job.metadata.extracted_title} at {job.metadata.extracted_company} | {job.metadata.extracted_location_status}\n\n"
            f"Scores:\n{job.technical_capability_score} - technical capability;\n{job.strategic_value_score} - strategic value.\n\n"
            f"Strategic reason: {job.strategic_reason}\n\n"
            f"Pros:\n{str.join("\n", job.analytics.pros)}\n\n"
            f"Cons:\n{str.join("\n", job.analytics.cons)}\n\n"
            f"Warns:\n{str.join("\n", job.analytics.warnings)}\n\n"
            f"Application link: {url}"
        )