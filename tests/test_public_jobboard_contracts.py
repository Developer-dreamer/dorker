"""Shared contracts for the SEEK and Job Bank public-market scrapers."""

from __future__ import annotations

import pytest

from src.scraping.scrapers.base import BaseScraper
from src.scraping.scrapers.jobbankca import JobBankCAScraper
from src.scraping.scrapers.seek import SeekScraper
from pipeline.publisher import ATS_DEDUP_PRIORITY
from scripts.run_pipeline import CONFIGS


@pytest.mark.parametrize(
    ("scraper_type", "slug"),
    [(JobBankCAScraper, "all"), (SeekScraper, "au")],
)
def test_custom_scrapers_preserve_standard_options(
    scraper_type: type[BaseScraper], slug: str
) -> None:
    scraper = scraper_type(
        slug,
        include_descriptions=False,
        proxy="http://proxy.example:8080",
    )

    assert scraper.include_descriptions is False
    assert scraper.proxy == "http://proxy.example:8080"


def test_jobboard_dedup_priorities_prefer_employer_sources() -> None:
    assert ATS_DEDUP_PRIORITY["seek"] > ATS_DEDUP_PRIORITY["workday"]
    assert ATS_DEDUP_PRIORITY["jobbankca"] > ATS_DEDUP_PRIORITY["seek"]


def test_jobbank_singleton_fails_closed_on_empty() -> None:
    assert CONFIGS["jobbankca"]["fail_closed_on_empty"] is True
