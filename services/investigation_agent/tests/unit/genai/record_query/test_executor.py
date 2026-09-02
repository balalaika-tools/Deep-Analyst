from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from investigation_agent.genai.record_query.executor import ExecutorLimits, execute_guarded_select
from investigation_agent.genai.record_query.schemas import ParameterType, SqlParameter, SqlPlan


class _Context:
    def __init__(self, value: Any) -> None:
        self.value = value

    async def __aenter__(self) -> Any:
        return self.value

    async def __aexit__(self, *_: object) -> None:
        return None


class _Cursor:
    def __init__(self) -> None:
        self.executions: list[tuple[str, Sequence[object] | None]] = []
        self._result_batch = [
            {
                "record_id": "bank:t-1",
                "case_id": "case-a",
                "content_hash": "c" * 64,
                "source_refs": [
                    {
                        "record_id": "bank:t-1",
                        "locator": {"kind": "field", "field": "payload"},
                    }
                ],
                "amount_minor": 500,
            }
        ]
        self._batch_read = False

    async def __aenter__(self) -> _Cursor:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(self, query: str, params: Sequence[object] | None = None) -> None:
        self.executions.append((query, params))

    async def fetchmany(self, size: int = 0) -> Sequence[Mapping[str, object]]:
        del size
        if self._batch_read:
            return []
        self._batch_read = True
        return self._result_batch

    async def fetchall(self) -> Sequence[Mapping[str, object]]:
        return [
            {
                "record_id": "bank:t-1",
                "case_id": "case-a",
                "content_hash": "c" * 64,
                "text": None,
                "payload": {"amount_minor": 500},
            }
        ]


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def transaction(self) -> _Context:
        return _Context(self)

    def cursor(self) -> _Cursor:
        return self._cursor


class _Pool:
    def __init__(self) -> None:
        self.cursor = _Cursor()
        self.checkouts = 0

    def connection(self, timeout: float | None = None) -> _Context:
        assert timeout is not None and timeout > 0
        self.checkouts += 1
        return _Context(_Connection(self.cursor))


def _plan(sql: str | None = None) -> SqlPlan:
    return SqlPlan(
        sql=sql
        or (
            "SELECT record_id, case_id, content_hash, source_refs, amount_minor "
            "FROM agent_read.transactions_v1 WHERE amount_minor >= $1"
        ),
        parameters=(SqlParameter(position=1, parameter_type=ParameterType.INTEGER, value=100),),
        expected_shape="rows",
    )


@pytest.mark.asyncio
async def test_executor_sets_scope_in_transaction_and_applies_an_outer_limit() -> None:
    pool = _Pool()

    result = await execute_guarded_select(
        pool=pool,
        case_id="case-a",
        plan=_plan(),
        deadline=asyncio.get_running_loop().time() + 5,
        limits=ExecutorLimits(max_rows=3, max_bytes=10_000),
    )

    assert result.status == "ok"
    assert len(result.rows) == 1
    assert result.rows[0].evidence_id.startswith("row:bank:t-1:")
    statements = [statement for statement, _params in pool.cursor.executions]
    assert statements[0] == "SET TRANSACTION READ ONLY"
    assert "set_config('app.case_id', $1, true)" in statements[1]
    assert statements[2].endswith("LIMIT $2")
    assert pool.cursor.executions[2][1] == (100, 4)


@pytest.mark.asyncio
async def test_policy_rejection_happens_before_pool_checkout() -> None:
    pool = _Pool()

    result = await execute_guarded_select(
        pool=pool,
        case_id="case-a",
        plan=SqlPlan(sql="SELECT 1", expected_shape="rows"),
        deadline=asyncio.get_running_loop().time() + 5,
    )

    assert result.status == "rejected"
    assert pool.checkouts == 0
