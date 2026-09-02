from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

import psycopg
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
            "SELECT record_id, content_hash, source_refs, amount_minor "
            "FROM agent_read.transactions_v1 WHERE amount_minor >= $1"
        ),
        parameters=(SqlParameter(position=1, parameter_type=ParameterType.INTEGER, value=100),),
        expected_shape="rows",
    )


@pytest.mark.asyncio
async def test_executor_sets_read_controls_and_applies_an_outer_limit() -> None:
    pool = _Pool()

    result = await execute_guarded_select(
        pool=pool,
        plan=_plan(),
        deadline=asyncio.get_running_loop().time() + 5,
        limits=ExecutorLimits(max_rows=3, max_bytes=10_000),
    )

    assert result.status == "ok"
    assert len(result.rows) == 1
    assert result.rows[0].evidence_id.startswith("row:bank:t-1:")
    statements = [statement for statement, _params in pool.cursor.executions]
    assert statements[0] == "SET TRANSACTION READ ONLY"
    assert "set_config('statement_timeout', $1, true)" in statements[1]
    assert statements[2].endswith("LIMIT $2")
    assert pool.cursor.executions[2][1] == (100, 4)


@pytest.mark.asyncio
async def test_policy_rejection_happens_before_pool_checkout() -> None:
    pool = _Pool()

    result = await execute_guarded_select(
        pool=pool,
        plan=SqlPlan(sql="SELECT 1", expected_shape="rows"),
        deadline=asyncio.get_running_loop().time() + 5,
    )

    assert result.status == "rejected"
    assert pool.checkouts == 0


class _RaisingPool(_Pool):
    def __init__(self, errors: list[BaseException]) -> None:
        super().__init__()
        self._errors = errors

    def connection(self, timeout: float | None = None) -> _Context:
        lease = super().connection(timeout)
        if self._errors:
            raise self._errors.pop(0)
        return lease


async def _no_sleep(_: float) -> None:
    return None


@pytest.mark.asyncio
async def test_cancellation_propagates_instead_of_becoming_a_result() -> None:
    pool = _RaisingPool([asyncio.CancelledError()])

    with pytest.raises(asyncio.CancelledError):
        await execute_guarded_select(
            pool=pool,
            plan=_plan(),
            deadline=asyncio.get_running_loop().time() + 5,
            sleep=_no_sleep,
        )


@pytest.mark.asyncio
async def test_text_array_parameters_are_bound_as_lists() -> None:
    pool = _Pool()
    plan = SqlPlan(
        sql=(
            "SELECT record_id, content_hash, source_refs, debtor_iban "
            "FROM agent_read.transactions_v1 WHERE debtor_iban = ANY($1)"
        ),
        parameters=(
            SqlParameter(
                position=1,
                parameter_type=ParameterType.TEXT_ARRAY,
                value=("DE01", "DE02"),
            ),
        ),
        expected_shape="rows",
    )

    assert plan.parameters[0].value == ("DE01", "DE02")
    assert plan.parameter_values() == (["DE01", "DE02"],)

    result = await execute_guarded_select(
        pool=pool,
        plan=plan,
        deadline=asyncio.get_running_loop().time() + 5,
    )

    assert result.status == "ok"
    assert pool.cursor.executions[2][1] == (["DE01", "DE02"], 201)


@pytest.mark.asyncio
async def test_statement_timeout_is_not_retried() -> None:
    pool = _RaisingPool([psycopg.errors.QueryCanceled("canceling statement due to timeout")])

    result = await execute_guarded_select(
        pool=pool,
        plan=_plan(),
        deadline=asyncio.get_running_loop().time() + 5,
        limits=ExecutorLimits(max_physical_attempts=3),
        sleep=_no_sleep,
    )

    assert result.status == "failed"
    assert result.diagnostic is not None and result.diagnostic.code == "query_timeout"
    assert result.physical_attempts == 1
    assert pool.checkouts == 1


@pytest.mark.asyncio
async def test_transient_error_is_retried_once() -> None:
    pool = _RaisingPool([psycopg.OperationalError("connection reset")])

    result = await execute_guarded_select(
        pool=pool,
        plan=_plan(),
        deadline=asyncio.get_running_loop().time() + 5,
        sleep=_no_sleep,
    )

    assert result.status == "ok"
    assert result.physical_attempts == 2


@pytest.mark.asyncio
async def test_exhausted_deadline_during_retry_reports_a_timeout_failure() -> None:
    pool = _RaisingPool([psycopg.OperationalError("connection reset")])
    deadline = asyncio.get_running_loop().time() + 1e-9

    result = await execute_guarded_select(
        pool=pool,
        plan=_plan(),
        deadline=deadline,
        limits=ExecutorLimits(max_physical_attempts=3),
        sleep=_no_sleep,
    )

    assert result.status == "failed"
    assert result.diagnostic is not None and result.diagnostic.code == "query_timeout"
    assert result.physical_attempts == 1
