"""Coordinate user-owned profile/library mutations within PostgreSQL transactions."""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from asyncpg import Connection, Pool


@asynccontextmanager
async def owner_transaction(pool: Pool, user_id: int) -> AsyncIterator[Connection]:
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("INSERT INTO users(id) VALUES($1) ON CONFLICT DO NOTHING", user_id)
        await conn.fetchval("SELECT id FROM users WHERE id=$1 FOR UPDATE", user_id)
        yield conn
