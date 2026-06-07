"""Verify the exception hierarchy is stable.

These are part of the public contract — third-party code catches them by
type, so renaming or reparenting must be a breaking change.
"""

import pytest

from jobhive.exceptions import (
    CompanyNotFoundError,
    JobHiveError,
    ManifestError,
    ScraperError,
    StorageError,
)


def test_jobhive_error_is_subclass_of_exception() -> None:
    assert issubclass(JobHiveError, Exception)


@pytest.mark.parametrize(
    "exc",
    [ManifestError, StorageError, ScraperError],
)
def test_top_level_errors_inherit_from_jobhive_error(exc: type) -> None:
    assert issubclass(exc, JobHiveError)


def test_company_not_found_is_a_scraper_error() -> None:
    assert issubclass(CompanyNotFoundError, ScraperError)
    assert issubclass(CompanyNotFoundError, JobHiveError)


def test_can_catch_all_with_jobhive_error() -> None:
    for exc in [ManifestError, StorageError, ScraperError, CompanyNotFoundError]:
        with pytest.raises(JobHiveError):
            raise exc("boom")


def test_exceptions_carry_message() -> None:
    err = ScraperError("greenhouse 503")
    assert "greenhouse" in str(err)
