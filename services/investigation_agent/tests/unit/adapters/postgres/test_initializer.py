from __future__ import annotations

from typing import Any

import pytest
from investigation_agent.adapters.postgres.initializer import (
    EvidenceSchemaMissing,
    initialize_database,
)


class _Context:
    def __init__(self, value: Any) -> None:
        self.value = value

    async def __aenter__(self) -> Any:
        return self.value

    async def __aexit__(self, *_: object) -> None:
        return None


class _Cursor:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.fetchall_calls = 0

    async def __aenter__(self) -> _Cursor:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(self, statement: str, params: object = None) -> None:
        del params
        self.statements.append(statement)

    async def fetchall(self) -> list[dict[str, object]]:
        self.fetchall_calls += 1
        if self.fetchall_calls == 1:
            return [{"object_name": "public.records", "object_id": None}]
        return [{"extname": "vector"}]


class _Connection:
    def __init__(self) -> None:
        self.raw_cursor = _Cursor()
        self.closed = False

    def transaction(self) -> _Context:
        return _Context(self)

    def cursor(self) -> _Cursor:
        return self.raw_cursor

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_missing_evidence_fails_before_any_agent_ddl(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _Connection()

    class _ConnectionFactory:
        @staticmethod
        async def connect(*args: object, **kwargs: object) -> _Connection:
            del args, kwargs
            return connection

    monkeypatch.setattr(
        "investigation_agent.adapters.postgres.initializer.AsyncConnection",
        _ConnectionFactory,
    )

    with pytest.raises(EvidenceSchemaMissing):
        await initialize_database(
            owner_dsn="postgresql://owner:secret@db/app",
            reader_password="reader-password-123",
            writer_password="writer-password-123",
        )

    assert connection.closed
    assert all("CREATE" not in statement.upper() for statement in connection.raw_cursor.statements)
