from abc import ABC, abstractmethod
from typing import AsyncGenerator

from domain.model import Job


class BaseScraper(ABC):

    @property
    @abstractmethod
    def source_name(self) -> str:
        """source name: Djinni, DOU, YCombinator"""
        pass

    @abstractmethod
    async def scrape_latest(self) -> AsyncGenerator[Job, None]:
        """
        Parses pages and returns object by object with yield
        """
        pass
