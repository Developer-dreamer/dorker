import json
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple, Dict

import aiosqlite
from pydantic import BaseModel

from .models import OpenAIBatchRecord, Purpose


class BatchItem(BaseModel):
    id: str
    batch_id: str
    job_id: str
    match_id: Optional[str] = None

    status: str = "PENDING"
    http_status_code: Optional[int] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    processed_at: Optional[str] = None

class BatchRepository:
    def __init__(self, conn: aiosqlite.Connection):
        self._conn: aiosqlite.Connection = conn

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def get_pending_batches(self) -> List[Tuple[str, Purpose]]:
        query = """
                SELECT id, purpose
                FROM openai_batches
                WHERE id = 'batch_6a8c5f34e8888190ab22be5c6a5fe459';
                """
        """WHERE status IN ('validating',
                                        'in_progress',
                                        'finalizing',
                                        'cancelling');"""

        async with self._conn.execute(query) as cursor:
            rows = await cursor.fetchall()

            return [(row[0], Purpose(row[1])) for row in rows]


    async def create_batch(self, batch: OpenAIBatchRecord, job_ids: List[str]) -> None:
        """Inserts an OpenAI batch record and its associated item line items in a single atomic transaction.

        Assumes `custom_id` passed to OpenAI matches `job_id`.
        """
        metadata_json: Optional[str] = (
            json.dumps(batch.custom_metadata)
            if batch.custom_metadata is not None
            else None
        )

        batch_query = """
            INSERT INTO openai_batches (
                id,
                endpoint,
                input_file_id,
                output_file_id,
                error_file_id,
                purpose,
                status,
                completion_window,
                total_requests,
                completed_requests,
                failed_requests,
                created_at,
                in_progress_at,
                expires_at,
                finalizing_at,
                completed_at,
                failed_at,
                cancelled_at,
                custom_metadata,
                last_polled_at,
                downloaded_at,
                processed_at,
                error_message
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT(id) DO UPDATE SET
                output_file_id = excluded.output_file_id,
                error_file_id = excluded.error_file_id,
                status = excluded.status,
                total_requests = excluded.total_requests,
                completed_requests = excluded.completed_requests,
                failed_requests = excluded.failed_requests,
                in_progress_at = excluded.in_progress_at,
                expires_at = excluded.expires_at,
                finalizing_at = excluded.finalizing_at,
                completed_at = excluded.completed_at,
                failed_at = excluded.failed_at,
                cancelled_at = excluded.cancelled_at,
                last_polled_at = excluded.last_polled_at,
                downloaded_at = excluded.downloaded_at,
                processed_at = excluded.processed_at,
                error_message = excluded.error_message;
        """

        batch_params = (
            batch.id,
            batch.endpoint,
            batch.input_file_id,
            batch.output_file_id,
            batch.error_file_id,
            batch.purpose.value,
            batch.status.value,
            batch.completion_window,
            batch.total_requests,
            batch.completed_requests,
            batch.failed_requests,
            batch.created_at,
            batch.in_progress_at,
            batch.expires_at,
            batch.finalizing_at,
            batch.completed_at,
            batch.failed_at,
            batch.cancelled_at,
            metadata_json,
            batch.last_polled_at,
            batch.downloaded_at,
            batch.processed_at,
            batch.error_message,
        )

        batch_items_query = """
        INSERT INTO openai_batch_items (
            id,
            batch_id,
            job_id,
            status
        ) VALUES (?, ?, ?, 'PENDING')
        ON CONFLICT(id) DO UPDATE SET
            batch_id = excluded.batch_id,
            status = excluded.status,
            http_status_code = NULL,
            error_code = NULL,
            error_message = NULL,
            processed_at = NULL;
        """

        # Batch item tuples: (custom_id, batch_id, job_id)
        batch_items_params = [(f"req_{jid}_{int(time.time())}", batch.id, jid) for jid in job_ids]

        async with self._conn.cursor() as cursor:
            # 1. Insert or update batch container record
            await cursor.execute(batch_query, batch_params)

            # 2. Bulk insert individual line items
            if batch_items_params:
                await cursor.executemany(batch_items_query, batch_items_params)

        print("[INFO] Record saved.")

    async def update_batch(self, batch: OpenAIBatchRecord) -> None:
        metadata_json: Optional[str] = (
                    json.dumps(batch.custom_metadata)
                    if batch.custom_metadata is not None
                    else None
                )

        batch_update_query = """
            UPDATE openai_batches
            SET
                output_file_id = ?,
                error_file_id = ?,
                status = ?,
                total_requests = ?,
                completed_requests = ?,
                failed_requests = ?,
                in_progress_at = ?,
                expires_at = ?,
                finalizing_at = ?,
                completed_at = ?,
                failed_at = ?,
                cancelled_at = ?,
                custom_metadata = ?,
                last_polled_at = ?,
                downloaded_at = ?,
                processed_at = ?,
                error_message = ?
            WHERE id = ?;
        """

        batch_params = (
            batch.output_file_id,
            batch.error_file_id,
            batch.status.value,
            batch.total_requests,
            batch.completed_requests,
            batch.failed_requests,
            batch.in_progress_at,
            batch.expires_at,
            batch.finalizing_at,
            batch.completed_at,
            batch.failed_at,
            batch.cancelled_at,
            metadata_json,
            batch.last_polled_at,
            batch.downloaded_at,
            batch.processed_at,
            batch.error_message,
            batch.id,  # WHERE id = ?
        )

        async with self._conn.cursor() as cursor:
            await cursor.execute(batch_update_query, batch_params)


    async def update_batch_items(self, items: List[BatchItem]) -> None:

        query = """
            UPDATE openai_batch_items
            SET
                batch_id = ?,
                job_id = ?,
                status = ?,
                http_status_code = ?,
                error_code = ?,
                error_message = ?,
                processed_at = ?
            WHERE id = ?;
        """

        batch_items_params = [
            (
                item.batch_id,
                item.job_id,
                item.status,
                item.http_status_code,
                item.error_code,
                item.error_message,
                item.processed_at,
                item.id)
            for item in items
        ]

        await self._conn.executemany(query, batch_items_params)

    async def get_batch_items(self, batch_id: str) -> List[BatchItem]:
        query = """
                SELECT * FROM openai_batch_items
                WHERE batch_id = ?;
                """

        self._conn.row_factory = aiosqlite.Row
        async with self._conn.execute(query, (batch_id,)) as cursor:
            rows = await cursor.fetchall()

            return [BatchItem(**dict(row)) for row in rows]
