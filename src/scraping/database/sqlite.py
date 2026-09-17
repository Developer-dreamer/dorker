import sqlite3
import tempfile
from pathlib import Path

from src.scraping.models import Job

from .base import description_keys

_CACHE_SCHEMA_VERSION = 2


class DescriptionCache:
    """Disk-backed description cache, optionally persistent and zstd-compressed."""

    def __init__(self, db_path: Path, ats_name: str, compress: bool = False) -> None:
        self.conn: sqlite3.Connection | None = None
        self.compress = compress
        self._compressor = None
        self._decompressor = None
        if compress:
            import zstandard

            self._compressor = zstandard.ZstdCompressor(level=3)
            self._decompressor = zstandard.ZstdDecompressor()

        with tempfile.NamedTemporaryFile(
            prefix="ats-scrapers-description-cache-",
            suffix=".sqlite3",
            delete=False,
        ) as tmp:
            self.path = Path(tmp.name)
        self._owns_tempfile = True

        try:
            self.conn = sqlite3.connect(self.path)
            if self._owns_tempfile:
                self.conn.execute("PRAGMA journal_mode=OFF")
                self.conn.execute("PRAGMA synchronous=OFF")
            else:
                self.conn.execute("PRAGMA journal_mode=WAL")
                self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA temp_store=MEMORY")

            current_user_version = self.conn.execute("PRAGMA user_version").fetchone()[0]
            existing_rows = 0
            existing_table = self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='descriptions'"
            ).fetchone()
            if existing_table is not None:
                existing_rows = self.conn.execute("SELECT COUNT(*) FROM descriptions").fetchone()[0]
            if existing_rows > 0 and current_user_version != _CACHE_SCHEMA_VERSION:
                if self.conn is not None:
                    self.conn.close()
                    self.conn = None
                if self._owns_tempfile:
                    self.path.unlink(missing_ok=True)
                raise RuntimeError(
                    f"DescriptionCache schema mismatch at {self.path}: "
                    f"file user_version={current_user_version}, "
                    f"code expects {_CACHE_SCHEMA_VERSION}. Delete the "
                    f"file and let the pipeline reseed it from the "
                    f"current jobs.csv (or run scripts/build_workday_cache "
                    f"if a backfill seed is available)."
                )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS descriptions (
                    kind TEXT NOT NULL,
                    cache_key TEXT NOT NULL,
                    description BLOB NOT NULL,
                    PRIMARY KEY (kind, cache_key)
                )
                """
            )
            self.conn.execute(f"PRAGMA user_version = {_CACHE_SCHEMA_VERSION}")
            self.count: int = self.conn.execute("SELECT COUNT(*) FROM descriptions").fetchone()[0]

            self._load_sql(db_path, ats_name)
        except Exception:
            if self.conn is not None:
                self.conn.close()
                self.conn = None
            if self._owns_tempfile:
                self.path.unlink(missing_ok=True)
            raise

    def _require_conn(self) -> sqlite3.Connection:
        if self.conn is None:
            raise RuntimeError("DescriptionCache connection is closed")
        return self.conn

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        if self._owns_tempfile:
            self.path.unlink(missing_ok=True)

    def _encode(self, description: str) -> bytes:
        raw = description.encode("utf-8")
        if self._compressor is not None:
            return self._compressor.compress(raw)
        return raw

    def _decode(self, blob: bytes) -> str:
        raw = self._decompressor.decompress(blob) if self._decompressor is not None else blob
        return raw.decode("utf-8")

    def _load_sql(self, db_path: Path, ats_name: str) -> None:
        cache_conn = self._require_conn()
        batch: list[tuple[str, str, bytes]] = []
        uri = f"{db_path.resolve().as_uri()}?mode=ro"

        try:
            with sqlite3.connect(uri, uri=True) as src_conn:
                src_conn.row_factory = sqlite3.Row
                cursor = src_conn.execute(
                    """
                    SELECT ats_id, ats_type, company_slug AS company, url, description
                    FROM jobs
                    WHERE ats_name = ?
                    AND description IS NOT NULL
                    AND description != ''
                    """,
                    (ats_name,),
                )
                for raw_row in cursor:
                    row = dict(raw_row)
                    description = row["description"].strip()
                    if not description:
                        continue

                    blob = self._encode(description)
                    job = Job.model_construct(**row)
                    for key in description_keys(job):
                        batch.append((*key, blob))
                    if len(batch) >= 2_000:
                        self._insert_many(batch)
                        batch.clear()
                if batch:
                    self._insert_many(batch)
        except (OSError, sqlite3.Error):
            cache_conn.execute("DELETE FROM descriptions")
            cache_conn.commit()

        self.count = cache_conn.execute("SELECT COUNT(*) FROM descriptions").fetchone()[0]

    def _insert_many(self, rows: list[tuple[str, str, bytes]], *, replace: bool = False) -> int:
        conn = self._require_conn()
        verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
        cur = conn.executemany(
            f"""
            {verb} INTO descriptions (kind, cache_key, description)
            VALUES (?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        return cur.rowcount

    def get(self, job: Job) -> str | None:
        conn = self._require_conn()
        for kind, key in description_keys(job):
            row = conn.execute(
                """
                SELECT description FROM descriptions
                WHERE kind = ? AND cache_key = ?
                """,
                (kind, key),
            ).fetchone()
            if row:
                return self._decode(row[0])
        return None

    def set(self, job: Job, description: str) -> None:
        conn = self._require_conn()
        blob = self._encode(description)
        rows = [(*key, blob) for key in description_keys(job)]
        if not rows:
            return
        existing = conn.execute(
            "SELECT COUNT(*) FROM descriptions WHERE (kind, cache_key) IN ("
            + ",".join("(?,?)" for _ in rows)
            + ")",
            [v for kind, key, _ in rows for v in (kind, key)],
        ).fetchone()[0]
        new_keys = len(rows) - existing
        self._insert_many(rows, replace=True)
        self.count += max(0, new_keys)
