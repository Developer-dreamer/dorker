from asyncpg import Pool

from src.database.protocols import BatchRepository
from src.shared.models import OpenAIBatchRecord


class BatchRepositoryPostgres(BatchRepository):
    def __init__(self, pool: Pool) -> None:
        self.pool = pool

    async def save_batch(self, batch: OpenAIBatchRecord) -> None:
        columns = (
            "id",
            "input_file_id",
            "output_file_id",
            "status",
            "items",
        )

        query = f"""
            INSERT INTO openai_batches ({", ".join(columns)})
            VALUES ({", ".join(f"${i + 1}" for i in range(len(columns)))});
        """

        payload = batch.model_dump()

        async with self.pool.acquire() as conn:
            await conn.execute(query, *(payload[col] for col in columns))
