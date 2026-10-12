import json
from uuid import UUID

from asyncpg import Pool

from src.shared.models.discussion import DiscussionMessage


class DiscussionRepositoryPostgres:
    def __init__(self, pool: Pool) -> None:
        self.pool = pool

    async def save(self, message: DiscussionMessage) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO discussion_messages
                   (id, match_id, user_id, chat_id, role, content, metadata, created_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)""",
                message.id,
                message.match_id,
                message.user_id,
                message.chat_id,
                message.role,
                message.content,
                json.dumps(message.metadata),
                message.created_at,
            )

    async def history(self, match_id: UUID, user_id: int, chat_id: int) -> list[DiscussionMessage]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM discussion_messages
                   WHERE match_id = $1 AND user_id = $2 AND chat_id = $3
                   ORDER BY created_at, id""",
                match_id,
                user_id,
                chat_id,
            )
        messages = []
        for row in rows:
            data = dict(row)
            if isinstance(data["metadata"], str):
                data["metadata"] = json.loads(data["metadata"])
            messages.append(DiscussionMessage.model_validate(data))
        return messages
