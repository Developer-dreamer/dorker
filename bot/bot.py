import os
import sys
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart

# Перевірка наявності токена перед запуском
BOT_TOKEN = os.getenv("TELEGRAM_API_KEY")
if not BOT_TOKEN:
    print("Error: TELEGRAM_API_KEY variable is not set.")
    sys.exit(1)

# Ініціалізація компонентів
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    """/start command processor"""
    await message.answer("Hi! , Send me any message")

@dp.message(F.text)
async def echo_and_print_message(message: types.Message):
    """
    Any text message processor
    """
    # Raw text for logging
    user_text = message.text
    # ChatID & username for logging
    chat_id = message.chat.id
    username = message.from_user.username or "unknown_user"
    
    print(f"\n[Received message] from @{username} (ID: {chat_id}):")
    print("-" * 40)
    print(user_text)
    print("-" * 40)