#!/usr/bin/env python3
"""Normalize the `description` column of a ats-scrapers jobs.csv in place,
streaming chunk-by-chunk so memory stays bounded.

Workflow:
  - Stream-read input CSV row by row
  - Buffer rows into chunks (default 2000)
  - Dispatch each chunk to a worker pool for parallel normalization
  - Stream-write to a temp file
  - On EOF: atomic rename temp → input
"""
from __future__ import annotations

import html
import re
from typing import Any

_MD = None
def _md_lazy():
    global _MD
    if _MD is None:
        from markdownify import markdownify as md
        _MD = md
    return _MD


_LINKIFY = None
def _linkify_lazy():
    """linkify-it-py is the canonical URL/email/IP detector and the same
    library markdown-it-py uses for its linkify plugin. Lazy-import so the
    worker subprocess only pays the import cost once per process."""
    global _LINKIFY
    if _LINKIFY is None:
        from linkify_it import LinkifyIt
        from linkify_it.tlds import TLDS
        instance = LinkifyIt()
        instance.tlds(TLDS)  # full ICANN/IANA TLD set
        _LINKIFY = instance
    return _LINKIFY


def autolink(text: str) -> str:
    """Wrap bare URLs and emails in CommonMark autolink syntax (``<url>``).

    Markdownify converts real ``<a href=…>`` anchors to ``[text](url)``
    already; this pass picks up the URLs that were rendered as plain
    text in the source (typical in plain-text job postings: "apply at
    https://acme.com/jobs"). We skip URLs that are already part of a
    markdown link, an existing autolink, or an image — splicing inside
    those would double-wrap.

    Email addresses get ``<addr@host>`` which most markdown renderers
    auto-link as ``mailto:`` links.
    """
    if not text:
        return text
    linkify = _linkify_lazy()
    matches = linkify.match(text)
    if not matches:
        return text
    # Walk matches in reverse so earlier indices stay valid as we splice.
    out = text
    for m in reversed(matches):
        start, end = m.index, m.last_index
        # Skip if already inside an existing markdown link or autolink
        # by checking the chars immediately around the match.
        before = out[max(0, start - 2):start]
        after = out[end:end + 2]
        if before.endswith("](") or before.endswith("<") or after.startswith(">"):
            continue
        # Skip the markdown image syntax ``![alt](url)`` too.
        if before.endswith("!"):
            continue
        # ``m.url`` is linkify-it's normalized form: emails get a
        # ``mailto:`` prefix automatically. Using it as the autolink
        # body would corrupt the visible text (``foo@bar.com`` would
        # render as ``mailto:foo@bar.com`` in viewers that don't
        # collapse the scheme). The raw substring in ``out`` is what
        # the source wrote, so use that and rely on CommonMark/GFM to
        # apply ``mailto:`` at render time.
        original = out[start:end]
        replacement = f"<{original}>"
        out = out[:start] + replacement + out[end:]
    return out


HTML_BLOCK_RE = re.compile(
    r"<(?:p|div|ul|ol|li|h[1-6]|br|table|tr|td|a|strong|em|b|i|span|section|article|hr|blockquote)\b",
    re.IGNORECASE,
)
HTML_ANY_TAG_RE = re.compile(r"<[a-z][a-z0-9]*\b[^>]*>", re.IGNORECASE)
HTML_ENTITY_RE = re.compile(r"&(?:nbsp|amp|lt|gt|quot|#\d+|[a-z]{2,8});", re.IGNORECASE)
BLANK_RUN_RE = re.compile(r"\n{3,}")
WS_RUN_RE = re.compile(r"\s+")


def normalize_one(s: str) -> str:
    """Pipeline: route to markdownify / strip-unescape / fast-path, then
    apply autolink to whatever ended up in the output. Bare URLs and
    emails that survived earlier branches become CommonMark autolinks.
    """
    if s is None:
        return None
    s = s.strip()
    if not s:
        return None
    if HTML_BLOCK_RE.search(s):
        try:
            out = _md_lazy()(
                s, heading_style="ATX", strip=["script", "style"],
                bullets="-", escape_underscores=False, wrap=False,
            )
        except Exception:
            out = re.sub(r"<[^>]+>", "", s)
            out = html.unescape(out)
        out = BLANK_RUN_RE.sub("\n\n", out).strip()
        return autolink(out) or None
    if HTML_ANY_TAG_RE.search(s):
        out = re.sub(r"<[^>]+>", "", s)
        out = html.unescape(out)
        out = WS_RUN_RE.sub(" ", out).strip()
        return autolink(out) or None
    if HTML_ENTITY_RE.search(s):
        out = html.unescape(s).strip()
        return autolink(out) or None
    return autolink(s)

