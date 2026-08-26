from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

from .scrapers.base import ScraperRegistry

logger = logging.getLogger(__name__)
CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "configs" / "ats_config.json"


# --- Extraction Helpers ---

def _slug_col(row: dict[str, Any]) -> str | None:
    slug = (row.get("slug") or "").strip()
    return slug or None

def _recruitee_slug(row: dict[str, Any]) -> str | None:
    url = (row.get("url") or "").strip()
    if slug := _slug_col(row):
        if url.startswith("http") and ".recruitee.com" not in urlparse(url).netloc:
            return url.rstrip("/")
        return slug.lower()
    if url.startswith("http"):
        m = re.match(r"https?://([a-z0-9][a-z0-9-]+)\.recruitee\.com", url, re.IGNORECASE)
        if m:
            return m.group(1).lower()
    return (row.get("name") or "").strip() or None

def _personio_slug(row: dict[str, Any]) -> str | None:
    if slug := _slug_col(row):
        return slug.lower()
    url = (row.get("url") or "").strip()
    if url.startswith("http"):
        m = re.match(r"https?://([a-z0-9][a-z0-9-]+)\.jobs\.personio\.com", url, re.IGNORECASE)
        if m:
            return m.group(1).lower()
    return (row.get("name") or "").strip() or None

def _avature_slug(row: dict[str, Any]) -> str | None:
    url = (row.get("url") or "").strip()
    if slug := _slug_col(row):
        if slug.startswith(("http://", "https://")):
            return slug
        if url.startswith("http"):
            parsed = urlparse(url)
            if parsed.scheme and parsed.netloc and parsed.path.rstrip("/") != "/careers/SearchJobs":
                return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}".rstrip("/")
        return slug.lower()
    if url.startswith("http"):
        m = re.match(r"https?://([a-z0-9][a-z0-9-]+)\.avature\.net", url, re.IGNORECASE)
        if m:
            return m.group(1).lower()
    return (row.get("name") or "").strip() or None

def _successfactors_slug(row: dict[str, Any]) -> str | None:
    url = (row.get("url") or "").strip()
    if url:
        return url.rstrip("/")
    return _slug_col(row) or (row.get("name") or "").strip() or None

def _rippling_slug(row: dict[str, Any]) -> str | None:
    if slug := _slug_col(row):
        return slug.lower()
    url = (row.get("url") or "").strip()
    if url.startswith("http"):
        m = re.match(r"https?://ats\.rippling\.com/([a-z0-9][a-z0-9-]+)", url, re.IGNORECASE)
        if m:
            return m.group(1).lower()
    return (row.get("name") or "").strip() or None

def _workable_slug(row: dict[str, Any]) -> str | None:
    if slug := _slug_col(row):
        return slug
    url = (row.get("url") or "").strip()
    if url.startswith("http"):
        m = re.match(r"https?://apply\.workable\.com/([^/?#]+)", url, re.IGNORECASE)
        if m:
            return m.group(1)
    return (row.get("name") or "").strip() or None

def _lever_slug(row: dict[str, Any]) -> str | None:
    url = (row.get("url") or "").strip()
    if url.startswith("http"):
        m = re.match(r"https?://jobs\.lever\.co/([^/?#]+)", url, re.IGNORECASE)
        if m:
            return unquote(m.group(1))
    return _slug_col(row) or (row.get("name") or "").strip() or None

def _greenhouse_slug(row: dict[str, Any]) -> str | None:
    if slug := _slug_col(row):
        return slug.lower()
    url = (row.get("url") or "").strip()
    if url.startswith("http"):
        m = re.match(r"https?://(?:job-boards|boards)\.greenhouse\.io/([^/?#]+)", url, re.IGNORECASE)
        if m:
            return m.group(1).lower()
    return (row.get("name") or "").strip() or None

def _ashby_slug(row: dict[str, Any]) -> str | None:
    if slug := _slug_col(row):
        return slug.lower()
    url = (row.get("url") or "").strip()
    if url.startswith("http"):
        m = re.match(r"https?://jobs\.ashbyhq\.com/([^/?#]+)", url, re.IGNORECASE)
        if m:
            return m.group(1).lower()
    return (row.get("name") or "").strip() or None

def _oracle_slug(row: dict[str, Any]) -> str | None:
    raw = (row.get("url") or "").strip()
    if not raw or not raw.startswith(("http://", "https://")):
        return raw or None
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw
    base = f"{parsed.scheme}://{parsed.netloc}"
    query_site = parse_qs(parsed.query).get("site_number")
    site = query_site[0] if (query_site and query_site[0]) else None
    if not site:
        m = re.search(r"/sites/([^/?#]+)", parsed.path)
        site = m.group(1) if m else None
    return f"{base}?site_number={site}" if site else base

def _icims_slug(row: dict[str, Any]) -> str | None:
    url = (row.get("url") or "").strip()
    if url:
        host = (urlparse(url).hostname or "").lower()
        m = re.fullmatch(r"careers-([a-z0-9-]+)\.icims\.com", host)
        if m:
            return m.group(1)
        if host.endswith(".icims.com"):
            return url.split("?", 1)[0].rstrip("/")
    return _slug_col(row) or (row.get("name") or "").strip() or None

def _eightfold_slug(row: dict[str, Any]) -> str | None:
    raw = (row.get("slug") or row.get("url") or row.get("name") or "").strip()
    return raw.replace("https://", "").replace("http://", "").split("/")[0].split(".")[0] or None

def _eightfold_kwargs(row: dict[str, Any]) -> dict[str, Any]:
    kw: dict[str, Any] = {}
    raw_url = (row.get("url") or "").strip()
    if raw_url.startswith("http"):
        parsed = urlparse(raw_url)
        kw["base_url"] = f"{parsed.scheme}://{parsed.netloc}" if (parsed.scheme and parsed.netloc) else raw_url.rstrip("/")
    if domain := (row.get("domain") or "").strip():
        kw["domain"] = domain
    if name := (row.get("name") or "").strip():
        kw["company_name"] = name
    return kw


# --- Extractor Registry Maps ---

SLUG_EXTRACTORS: dict[str, Callable[[dict[str, Any]], str | None]] = {
    "default": lambda r: _slug_col(r) or (r.get("name") or "").strip() or None,
    "url": lambda r: (r.get("url") or "").strip() or None,
    "slug_or_name": lambda r: _slug_col(r) or r.get("name"),
    "slug_or_url": lambda r: _slug_col(r) or (r.get("url") or "").strip() or None,
    "slug_or_name_or_url": lambda r: _slug_col(r) or r.get("name") or r.get("url"),
    "slug_or_url_or_name": lambda r: _slug_col(r) or r.get("url") or r.get("name"),
    "fixed_mercor": lambda r: "mercor",
    "gem": lambda r: _slug_col(r) or ((r.get("url") or "").rstrip("/").rsplit("/", 1)[-1] if (r.get("url") or "").strip() else (r.get("name") or "").strip()),
    "taleo": lambda r: ((r.get("url") or "").strip() if (r.get("url") or "").startswith("http") else f"https://{(r.get('url') or '').strip()}" if r.get("url") else None),
    "join_com": lambda r: (_slug_col(r) or "").lower() or (r.get("url") or "").rstrip("/").rsplit("/", 1)[-1].lower() or None,
    "recruitee": _recruitee_slug,
    "personio": _personio_slug,
    "avature": _avature_slug,
    "successfactors": _successfactors_slug,
    "rippling": _rippling_slug,
    "workable": _workable_slug,
    "lever": _lever_slug,
    "greenhouse": _greenhouse_slug,
    "ashby": _ashby_slug,
    "oracle": _oracle_slug,
    "icims": _icims_slug,
    "eightfold": _eightfold_slug,
}

KWARGS_EXTRACTORS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "none": lambda r: {},
    "company_name": lambda r: {"company_name": (r.get("name") or "").strip() or None},
    "company_name_legacy": lambda r: {"company_name": (r.get("company_name") or "").strip() or None},
    "workday": lambda r: {
        "max_fetch_seconds": float(os.environ.get("ATS_SCRAPERS_WORKDAY_TENANT_TIMEOUT", "900")),
        "company_name": (r.get("name") or "").strip() or None,
    },
    "eightfold": _eightfold_kwargs,
}


# --- Dynamic Manager Singleton ---

class DynamicConfigManager:
    def __init__(self, config_path: Path = CONFIG_PATH):
        self._config_path = config_path
        self._last_mtime: float = 0.0
        self._cached_configs: dict[str, dict[str, Any]] = {}
        self.reload()

    def reload(self) -> None:
        if not self._config_path.exists():
            logger.error(f"Configuration file not found: {self._config_path}")
            return
        try:
            mtime = os.path.getmtime(self._config_path)
            if mtime == self._last_mtime:
                return

            with open(self._config_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)

            defaults = raw_data.pop("_defaults", {})
            resolved: dict[str, dict[str, Any]] = {}

            for ats, raw_cfg in raw_data.items():
                cfg = {**defaults, **raw_cfg}

                slug_fn = SLUG_EXTRACTORS.get(cfg.get("slug_extractor", "default"), SLUG_EXTRACTORS["default"])
                kwargs_fn = KWARGS_EXTRACTORS.get(cfg.get("kwargs_extractor", "none"), KWARGS_EXTRACTORS["none"])

                # Resolve scraper class from registry
                scraper_cls = None
                if ScraperRegistry.has_scraper(ats):
                    scraper_cls = ScraperRegistry.get(ats)

                resolved[ats] = {
                    **cfg,
                    "scraper": scraper_cls,
                    "slug": slug_fn,
                    "kwargs": kwargs_fn,
                }

            self._cached_configs = resolved
            self._last_mtime = mtime
            logger.info("DynamicConfigManager successfully reloaded ats_config.json")
        except Exception as e:
            logger.error(f"Failed to reload configuration: {e}")

    def get(self, ats: str) -> dict[str, Any]:
        self.reload()
        return self._cached_configs.get(ats, {})

    def is_enabled(self, ats: str) -> bool:
        self.reload()
        return bool(self._cached_configs.get(ats, {}).get("enabled", False))

    def all_configs(self) -> dict[str, dict[str, Any]]:
        self.reload()
        return self._cached_configs

    def __getitem__(self, ats: str) -> dict[str, Any]:
        return self.get(ats)

    def keys(self) -> Any:
        self.reload()
        return self._cached_configs.keys()


# Global exported proxy instance
CONFIGS = DynamicConfigManager()
