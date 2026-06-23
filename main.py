import asyncio
from asyncio import Queue
from typing import Any

from aiogram import Dispatcher

from src.app.engine import ApplicationEngine
from src.config.env import Env, get_envs, load_profile, load_prompts
from src.config.logger import Logger, configure_logger
from src.domain.model.prompt import PromptStructure
from src.domain.service.analyst import JobAnalyst
from src.infra.ai.gemini import GeminiFlashClient
from src.infra.bot.handler import BotHandler
from src.infra.db.sql_lite.job_repository import JobRepository
from src.infra.db.sql_lite.match_repository import MatchRepository

if __name__ == "__main__":

    env: Env = get_envs()
    logger: Logger = configure_logger()
    queue: Queue[Any] = Queue()
    dispatcher = Dispatcher()

    try:
        prompts: PromptStructure = load_prompts(env.paths)
        profile: str = load_profile(env.paths)
    except FileNotFoundError:
        print("File with prompt configurations was not found. Exiting...")
        exit(1)

    job_repo = JobRepository(logger, env.db)
    match_repo = MatchRepository(logger, env.db)
    gemini_client = GeminiFlashClient(env.gemini_api_key, logger)
    analyst = JobAnalyst(logger, gemini_client, job_repo, match_repo, prompts, profile)
    bot_handler = BotHandler(logger, env.bot_token, dispatcher, queue)
    engine = ApplicationEngine(logger, bot_handler, analyst, queue)

    try:
        asyncio.run(engine.start())

    except (KeyboardInterrupt, SystemExit):
        print("Bot stopped")
