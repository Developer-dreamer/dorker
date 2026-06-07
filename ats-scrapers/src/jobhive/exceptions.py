"""Exception hierarchy for jobhive."""


class JobHiveError(Exception):
    """Base class for all jobhive errors."""


class ManifestError(JobHiveError):
    """Raised when the dataset manifest cannot be fetched or parsed."""


class StorageError(JobHiveError):
    """Raised when reading from or writing to remote storage fails."""


class ScraperError(JobHiveError):
    """Raised when an ATS scraper fails to fetch or parse jobs."""


class CompanyNotFoundError(ScraperError):
    """Raised when a company is not present on the requested ATS."""
