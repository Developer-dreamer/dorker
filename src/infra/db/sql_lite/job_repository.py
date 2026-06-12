from typing import Any, AsyncGenerator

import aiosqlite

from src.config.logger import Logger


class JobRepository:
    def __init__(self, logger: Logger, db_path: str):
        self.db_path = db_path
        self.logger = logger

    async def get_relevant_jobs(self, batch_size: int = 10) -> AsyncGenerator[dict[str, Any], None]:
        query = """
                SELECT j.id, jobs_fts.title, j.company_slug, j.location, jobs_fts.description, j.url, j.posted_at, j.is_remote
                FROM jobs_fts
                        JOIN jobs j ON j.id = jobs_fts.ROWID
                WHERE (
                    j.is_remote = 'KEEP_GLOBAL' OR
                    j.is_remote = 'KEEP_PURE' OR
                    j.is_remote = 'POTENTIAL_PURE')
                AND (
                    j.posted_at < ?
                    OR (j.posted_at = ? AND j.id < ?)
                )
                AND jobs_fts MATCH '
                                    (Golang OR "Go engineer" OR "Go developer" OR "Go backend" OR "Go language" OR "Go stack")
                                    NOT title : (Lead OR Principal OR Staff OR Director OR Architect OR Manager OR VP OR Head OR "Senior-Lead")
                                    NOT title : Frontend'
                AND j.posted_at >= date('now', '-1 month')
                ORDER BY j.posted_at DESC, j.id DESC
                LIMIT ?;
                """

        last_posted_at = "9999-12-31 23:59:59"
        last_id = 9223372036854775807

        while True:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row

                params = (last_posted_at, last_posted_at, last_id, batch_size)

                self.logger.info("Executing query", query=query)
                async with db.execute(query, params) as cursor:
                        rows = await cursor.fetchall()

                        if not rows:
                            break

                        for row in rows:
                            job_dict = dict(row)
                            last_posted_at = job_dict['posted_at']
                            last_id = job_dict['id']
                            yield job_dict
