"""Extensions and schema creation. No Alembic: the prototype rebuilds, it never migrates."""

from __future__ import annotations

import evidence_model  # noqa: F401  (registers every table on SQLModel.metadata)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlmodel import SQLModel

REQUIRED_EXTENSIONS = ("vector", "pg_search")


async def ensure_extensions(conn: AsyncConnection) -> None:
    for extension in REQUIRED_EXTENSIONS:
        await conn.execute(text(f"CREATE EXTENSION IF NOT EXISTS {extension}"))


async def create_schema(conn: AsyncConnection) -> None:
    await conn.run_sync(SQLModel.metadata.create_all)
