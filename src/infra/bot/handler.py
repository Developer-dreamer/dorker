from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from config.logger import Logger


class BotHandler:
    def __init__(self, logger: Logger, bot_token: str, dispatcher: Dispatcher):
        self.logger = logger
        self.bot = Bot(token=bot_token)
        self.dp = dispatcher

        self.dp.message(CommandStart())(self.cmd_start)
        self.dp.message(F.text)(self.echo_and_print_message)

    async def cmd_start(self, message: types.Message) -> None:
        """/start command processor"""
        await message.answer("Hi! , Send me any message")

    async def echo_and_print_message(self, message: types.Message) -> None:
        """
        Any text message processor
        """
        user_text = message.text
        chat_id = message.chat.id
        username = message.from_user.username or "unknown_user"

        print(f"\n[Received message] from @{username} (ID: {chat_id}):")
        print("-" * 40)
        print(user_text)
        print("-" * 40)
