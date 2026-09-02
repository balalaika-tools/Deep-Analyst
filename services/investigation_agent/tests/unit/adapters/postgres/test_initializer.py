from __future__ import annotations

from typing import Any

import pytest
from investigation_agent.adapters.postgres.initializer import (
    REQUIRED_EVIDENCE_INDEXES,
    REQUIRED_EVIDENCE_RELATIONS,
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

    with pytest.raises(EvidenceSchemaMissing) as error:
        await initialize_database(
            owner_dsn="postgresql://owner:secret@db/app",
            reader_password="reader-password-123",
            writer_password="writer-password-123",
        )

    assert "public.records" in str(error.value)
    assert connection.closed
    assert all("CREATE" not in statement.upper() for statement in connection.raw_cursor.statements)


class _RelationsPresentCursor(_Cursor):
    async def fetchall(self) -> list[dict[str, object]]:
        self.fetchall_calls += 1
        if self.fetchall_calls == 1:
            return [
                {"object_name": name, "object_id": index + 1, "relkind": "r"}
                for index, name in enumerate(REQUIRED_EVIDENCE_RELATIONS)
            ] + [
                {"object_name": name, "object_id": 100 + index, "relkind": "i"}
                for index, name in enumerate(REQUIRED_EVIDENCE_INDEXES)
            ]
        if self.fetchall_calls == 2:
            return [{"extname": "vector"}]
        return [{"table_name": "records", "column_name": "record_id"}]


@pytest.mark.asyncio
async def test_missing_extensions_and_columns_are_named(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _Connection()
    connection.raw_cursor = _RelationsPresentCursor()

    class _ConnectionFactory:
        @staticmethod
        async def connect(*args: object, **kwargs: object) -> _Connection:
            del args, kwargs
            return connection

    monkeypatch.setattr(
        "investigation_agent.adapters.postgres.initializer.AsyncConnection",
        _ConnectionFactory,
    )

    with pytest.raises(EvidenceSchemaMissing) as error:
        await initialize_database(
            owner_dsn="postgresql://owner:secret@db/app",
            reader_password="reader-password-123",
            writer_password="writer-password-123",
        )

    message = str(error.value)
    assert "pg_search" in message
    assert "public.records:columns" in message
    assert "public.chunks:columns" in message
    assert "vector" not in message.replace("pg_search", "")


@pytest.mark.asyncio
async def test_current_schema_still_rotates_serving_role_passwords(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection()

    class _ExistingRolesCursor(_Cursor):
        async def fetchall(self) -> list[dict[str, object]]:
            return [{"rolname": "agent_reader"}, {"rolname": "agent_writer"}]

    connection.raw_cursor = _ExistingRolesCursor()

    class _ConnectionFactory:
        @staticmethod
        async def connect(*args: object, **kwargs: object) -> _Connection:
            del args, kwargs
            return connection

    async def evidence_schema_is_current(connection: object) -> None:
        del connection

    async def current_version(connection: object) -> str:
        del connection
        return "agent-runtime@2"

    monkeypatch.setattr(
        "investigation_agent.adapters.postgres.initializer.AsyncConnection",
        _ConnectionFactory,
    )
    monkeypatch.setattr(
        "investigation_agent.adapters.postgres.initializer._verify_evidence_schema",
        evidence_schema_is_current,
    )
    monkeypatch.setattr(
        "investigation_agent.adapters.postgres.initializer._recorded_version",
        current_version,
    )

    result = await initialize_database(
        owner_dsn="postgresql://owner:secret@db/app",
        reader_password="rotated-reader-password",
        writer_password="rotated-writer-password",
    )

    statements = [
        statement.as_string() if hasattr(statement, "as_string") else str(statement)
        for statement in connection.raw_cursor.statements
    ]
    assert result.changed is False
    assert any(
        'ALTER ROLE "agent_reader"' in statement and "rotated-reader-password" in statement
        for statement in statements
    )
    assert any(
        'ALTER ROLE "agent_writer"' in statement and "rotated-writer-password" in statement
        for statement in statements
    )
