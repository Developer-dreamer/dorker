class NotFoundException(Exception):
    pass


class DiscussionConflictException(Exception):
    """Invalid lifecycle operation; the message must be safe to show to the client."""


class DiscussionReplyException(Exception):
    """A reply failed with known user-message persistence, independent of reply persistence."""

    def __init__(self, *, saved: bool) -> None:
        super().__init__("Reply failed. Reload the discussion before retrying.")
        self.saved = saved


class ApplicationGenerationException(Exception):
    """Known generation failure; provider details are never included in the HTTP response."""


class JobScrapeException(Exception):
    """Known scraping or classification failure; provider details are not leaked in the response."""
