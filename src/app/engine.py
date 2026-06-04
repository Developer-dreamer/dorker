from asyncio import Queue

from config.logger import Logger
from domain.interface.abc_scraper import BaseScraper, ExtractedJobDTO
from domain.service.analyst import JobAnalyst
from infra.bot.handler import BotHandler


class ApplicationEngine:
    def __init__(self, logger: Logger, scrapers: list[BaseScraper], telegram_bot: BotHandler, analyst: JobAnalyst):
        self.logger = logger
        self.scrapers = scrapers
        self.job_queue: Queue[ExtractedJobDTO] = Queue()
        self.telegram_bot = telegram_bot
        self.analyst = analyst

    