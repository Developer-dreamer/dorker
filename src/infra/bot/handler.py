from asyncio import Queue

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

from src.config.logger import Logger


class BotHandler:
    def __init__(self, logger: Logger, bot_token: str, dispatcher: Dispatcher, cmd_queue: Queue):
        self.logger = logger
        self.bot = Bot(token=bot_token)
        self.dp = dispatcher
        self.cmd_queue = cmd_queue

        self.dp.message(Command("find"))(self.cmd_find_matches)

    async def cmd_find_matches(self, message: types.Message) -> None:
        """
        Intercepts the /find command, immediately acknowledges the user,
        and dispatches an event to the internal command queue to avoid blocking.
        """
        await message.answer(
            "🤖 Request received. Analyzing recent job postings against your profile. "
            "This process requires heavy data processing and will take a moment..."
        )

        event = {
            "chat_id": message.chat.id,
            "command": "FIND_TOP_MATCHES",
            "payload": {}
        }

        self.cmd_queue.put_nowait(event)

    async def handle_generic_message(self, message: types.Message) -> None:
        await message.answer("Use the /find command to initiate the orchestration pipeline.")

    async def send_direct_message(self, chat_id: int, text: str) -> None:
        """
        Provides an external interface for the orchestrator to push telemetry 
        and match reports directly to the user. Truncates text if it exceeds Telegram limits.
        """
        try:
            if len(text) > 4000:
                text = text[:3900] + "\n\n...[Content truncated due to Telegram character limits]"
            await self.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
        except Exception as e:
            self.logger.error(f"Failed to send telegram message to chat_id {chat_id}: {str(e)}")

    async def start_polling(self) -> None:
        self.logger.info("Starting Telegram Bot polling loop...")
        await self.dp.start_polling(self.bot)
