"""Disposable PostgreSQL lifecycle for integration tests.

Requires TEST_DATABASE_URL, a test-scoped database on postgres-app such as
postgresql+psycopg://app:<password>@127.0.0.1:5432/app_test. The fixture refuses a
database whose name does not contain "test" because it drops every table.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from ingestion.db.indexes import bootstrap_store
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlmodel import SQLModel

TEST_DIMENSIONS = 4


INTEGRATION_ROOT = Path(__file__).resolve().parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    # The hook sees every collected item; only this profile's tests get the marker.
    for item in items:
        if Path(str(item.path)).resolve().is_relative_to(INTEGRATION_ROOT):
            item.add_marker(pytest.mark.integration)


@pytest.fixture
def test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.fail("TEST_DATABASE_URL must point at a disposable test database")
    if "test" not in (make_url(url).database or ""):
        pytest.fail("TEST_DATABASE_URL must name a database containing 'test'")
    return url


@pytest_asyncio.fixture
async def engine(test_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(test_database_url, pool_size=2, max_overflow=1)
    async with engine.begin() as conn:
        # Agent integration tests create views over the ingestion tables. Remove
        # those test-only schemas first so this fixture remains repeatable when
        # both suites share the same disposable database.
        await conn.execute(text("DROP SCHEMA IF EXISTS agent_read CASCADE"))
        await conn.execute(text("DROP SCHEMA IF EXISTS agent_runtime CASCADE"))
        await conn.execute(text("DROP SCHEMA IF EXISTS bank_raw CASCADE"))
        await conn.run_sync(SQLModel.metadata.drop_all)
        await bootstrap_store(conn, embedding_dimensions=TEST_DIMENSIONS)
    try:
        yield engine
    finally:
        await engine.dispose()
