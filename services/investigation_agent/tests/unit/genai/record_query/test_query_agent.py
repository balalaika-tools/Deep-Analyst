"""Nested ``query_records`` agent behaviour with a scripted chat model and fake pool."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import psycopg
import pytest
from investigation_agent.genai.record_query.agent import QueryAgentPolicy, QueryRecordsAgent
from investigation_agent.genai.record_query.executor import ExecutorLimits
from investigation_agent.genai.record_query.schemas import QueryIntent
from investigation_agent.genai.shared.retries import CancellationToken, RetryPolicy
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.errors import NodeCancelledError

POLICY = RetryPolicy(
    max_attempts=2, initial_delay_s=0, backoff_factor=1, max_delay_s=0, jitter=False
)
SAFE_SQL = "SELECT record_id, content_hash, source_refs, amount_minor FROM agent_read.transactions_v1 WHERE amount_minor >= $1"
ROW = {
    "record_id": "bank:t-1",
    "content_hash": "c" * 64,
    "source_refs": [{"record_id": "bank:t-1", "locator": {"kind": "field", "field": "payload"}}],
    "amount_minor": 500,
}
RECORD = {
    "record_id": "bank:t-1",
    "content_hash": "c" * 64,
    "text": None,
    "payload": {"amount_minor": 500},
}


class ScriptedChatModel(BaseChatModel):
    responses: list[AIMessage]
    calls: int = 0
    seen: list[list[BaseMessage]] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(
        self, tools: Sequence[Any], *, tool_choice: str | None = None, **kwargs: Any
    ) -> Any:
        del tools, tool_choice, kwargs
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        self.seen.append(list(messages))
        self.calls += 1
        template = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        message = AIMessage(
            content=template.content,
            tool_calls=[{**c, "id": f"{c['id']}-{self.calls}"} for c in template.tool_calls],
            id=f"ai-{self.calls}",
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


def sql_call(sql: str, *, value: int = 100) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "execute_sql",
                "args": {
                    "sql": sql,
                    "parameters": [{"position": 1, "parameter_type": "integer", "value": value}],
                },
                "id": "s",
                "type": "tool_call",
            }
        ],
    )


def verdict(status: str, *ids: str, reason: str = "sufficient") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "QueryVerdict",
                "args": {
                    "status": status,
                    "selected_row_ids": list(ids),
                    "safe_reason_code": reason,
                },
                "id": "v",
                "type": "tool_call",
            }
        ],
    )


class _Context:
    def __init__(self, value: Any) -> None:
        self.value = value

    async def __aenter__(self) -> Any:
        return self.value

    async def __aexit__(self, *_: object) -> None:
        return None


@dataclass
class FakeCursor:
    pool: FakePool
    executions: list[tuple[str, Sequence[object] | None]] = field(default_factory=list)
    _served: bool = False

    async def __aenter__(self) -> FakeCursor:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(self, query: str, params: Sequence[object] | None = None) -> None:
        self.executions.append((query, params))
        if "agent_result" in query:
            self.pool.executed_statements.append(query)
            if self.pool.transient_failures:
                self.pool.transient_failures -= 1
                raise psycopg.OperationalError("connection dropped")
            if self.pool.schema_failures:
                self.pool.schema_failures -= 1
                raise psycopg.errors.UndefinedColumn("column does not exist")

    async def fetchmany(self, size: int = 0) -> Sequence[Mapping[str, object]]:
        del size
        if self._served or self.pool.empty:
            return []
        self._served = True
        return [dict(ROW)]

    async def fetchall(self) -> Sequence[Mapping[str, object]]:
        return [dict(RECORD)]


class FakeConnection:
    def __init__(self, pool: FakePool) -> None:
        self._pool = pool

    def transaction(self) -> _Context:
        return _Context(self)

    def cursor(self) -> FakeCursor:
        cursor = FakeCursor(self._pool)
        self._pool.cursors.append(cursor)
        return cursor


@dataclass
class FakePool:
    transient_failures: int = 0
    schema_failures: int = 0
    empty: bool = False
    checkouts: int = 0
    cursors: list[FakeCursor] = field(default_factory=list)
    executed_statements: list[str] = field(default_factory=list)

    def connection(self, timeout: float | None = None) -> _Context:
        assert timeout is not None and timeout > 0
        self.checkouts += 1
        return _Context(FakeConnection(self))


def _intent() -> QueryIntent:
    return QueryIntent(
        question="Which transfers exceed 100?",
        objective="list large transfers",
        desired_result_shape="rows",
    )


def _agent(
    responses: list[AIMessage], pool: FakePool, *, policy: QueryAgentPolicy | None = None
) -> tuple[QueryRecordsAgent, ScriptedChatModel]:
    model = ScriptedChatModel(responses=responses, seen=[])
    agent = QueryRecordsAgent(
        model=model,
        reader_pool=pool,
        executor_limits=ExecutorLimits(max_rows=10, max_bytes=10_000),
        retry_policy=POLICY,
        transient_errors=(TimeoutError,),
        policy=policy,
    )
    return agent, model


async def _run(
    agent: QueryRecordsAgent,
    *,
    progress: list[Mapping[str, object]] | None = None,
    cancellation: CancellationToken | None = None,
) -> Any:
    return await agent.run(
        _intent(),
        call_id="call-1",
        deadline=asyncio.get_running_loop().time() + 5,
        cancellation=cancellation or CancellationToken.create(),
        progress=None if progress is None else progress.append,
    )


@pytest.mark.asyncio
async def test_successful_first_plan_returns_rows_as_bounded_typed_evidence() -> None:
    pool = FakePool()
    agent, model = _agent([sql_call(SAFE_SQL), verdict("query_sufficient", "row:bank:t-1:*")], pool)
    progress: list[Mapping[str, object]] = []

    outcome = await _run(agent, progress=progress)

    assert (
        len(outcome.attempts) == 1
        and outcome.attempts[0].outcome == "ok"
        and outcome.attempts[0].row_count == 1
    )
    assert progress[0] == {"phase": "querying_records", "tool": "query_records", "attempt": 1}
    assert "<untrusted-evidence" in str(model.seen[1][-1].content)
    assert "agent_read.transactions_v1" in str(model.seen[0][-1].content)
    assert outcome.consumption.rows == 1 and outcome.consumption.tool_calls == 1


@pytest.mark.asyncio
async def test_verdict_selects_only_returned_rows() -> None:
    pool = FakePool()
    first, _ = _agent([sql_call(SAFE_SQL)], pool)
    probe = await first.run(
        _intent(),
        call_id="c",
        deadline=asyncio.get_running_loop().time() + 5,
        cancellation=CancellationToken.create(),
    )
    del probe
    # Learn the deterministic row id by running the executor once through a scripted verdict.
    pool = FakePool()
    agent, model = _agent(
        [sql_call(SAFE_SQL), verdict("query_sufficient", "row:bank:t-1:unknown")], pool
    )
    outcome = await _run(agent)
    assert outcome.status == "query_exhausted"
    assert (
        "unreturned_selection_dropped" in outcome.warnings
        and "sufficient_without_selection" in outcome.warnings
    )

    returned = str(model.seen[1][-1].content)
    row_id = returned.split("id='")[1].split("'")[0]
    pool = FakePool()
    agent, _ = _agent([sql_call(SAFE_SQL), verdict("query_sufficient", row_id)], pool)
    outcome = await _run(agent)
    assert outcome.status == "query_sufficient" and [e.evidence_id for e in outcome.evidence] == [
        row_id
    ]
    assert outcome.evidence[0].content_hash == "c" * 64


@pytest.mark.asyncio
async def test_policy_rejection_is_corrected_without_database_io() -> None:
    pool = FakePool()
    agent, model = _agent(
        [sql_call("SELECT * FROM public.records"), sql_call(SAFE_SQL), verdict("query_sufficient")],
        pool,
    )

    outcome = await _run(agent)

    assert [a.outcome for a in outcome.attempts] == ["rejected", "ok"]
    assert outcome.attempts[0].diagnostic is not None
    assert outcome.attempts[0].diagnostic.code == "relation_not_allowed"
    assert pool.checkouts == 1
    rejection = str(model.seen[1][-1].content)
    assert '"code": "relation_not_allowed"' in rejection


@pytest.mark.asyncio
async def test_schema_error_and_empty_result_are_safe_revisable_classes() -> None:
    pool = FakePool(schema_failures=1)
    agent, model = _agent(
        [
            sql_call(SAFE_SQL),
            sql_call(SAFE_SQL, value=200),
            verdict("query_exhausted", reason="insufficient"),
        ],
        pool,
    )

    outcome = await _run(agent)

    assert [a.outcome for a in outcome.attempts] == ["failed", "ok"]
    assert (
        outcome.attempts[0].diagnostic is not None
        and outcome.attempts[0].diagnostic.code == "schema_mismatch"
    )
    assert "column does not exist" not in str(model.seen[1][-1].content)

    empty_pool = FakePool(empty=True)
    agent, model = _agent(
        [sql_call(SAFE_SQL), verdict("query_exhausted", reason="insufficient")], empty_pool
    )
    outcome = await _run(agent)
    assert outcome.attempts[0].outcome == "empty" and outcome.evidence == ()
    assert '"failure_class": "empty"' in str(model.seen[1][-1].content)


@pytest.mark.asyncio
async def test_transient_physical_retry_keeps_one_semantic_plan() -> None:
    pool = FakePool(transient_failures=1)
    agent, _ = _agent([sql_call(SAFE_SQL), verdict("query_sufficient")], pool)

    outcome = await _run(agent)

    assert len(outcome.attempts) == 1 and outcome.attempts[0].physical_attempts == 2
    assert outcome.attempts[0].outcome == "ok"
    assert len(pool.executed_statements) == 2


@pytest.mark.asyncio
async def test_repeated_plan_is_rejected_without_io_and_plans_are_capped_at_three() -> None:
    pool = FakePool(empty=True)
    responses = [
        sql_call(SAFE_SQL),
        sql_call(SAFE_SQL),
        sql_call(SAFE_SQL, value=200),
        sql_call(SAFE_SQL, value=300),
        sql_call(SAFE_SQL, value=400),
        verdict("query_exhausted", reason="attempts_exhausted"),
    ]
    agent, model = _agent(responses, pool)

    outcome = await _run(agent)

    # The tool-call limit counts every execute_sql call, so a rejected repeat spends one of three.
    assert [a.outcome for a in outcome.attempts] == ["empty", "empty"]
    assert "repeated_plan_rejected" in outcome.warnings
    assert pool.checkouts == 2
    assert '"code": "repeated_plan"' in str(model.seen[2][-1].content)
    assert outcome.status == "query_exhausted"


@pytest.mark.asyncio
async def test_cancellation_stops_new_plans() -> None:
    pool = FakePool()
    cancellation = CancellationToken.create()
    cancellation.cancel()
    agent, _ = _agent([sql_call(SAFE_SQL), verdict("query_sufficient")], pool)

    with pytest.raises((asyncio.CancelledError, NodeCancelledError)):
        await _run(agent, cancellation=cancellation)

    assert pool.checkouts == 0


@pytest.mark.asyncio
async def test_row_limits_are_enforced_by_the_executor_not_the_model() -> None:
    pool = FakePool()
    agent, _ = _agent([sql_call(SAFE_SQL), verdict("query_sufficient")], pool)

    outcome = await _run(agent)

    assert outcome.attempts[0].outcome == "ok"
    statement = pool.executed_statements[0]
    assert statement.startswith("SELECT * FROM (") and statement.endswith("LIMIT $2")
    assert pool.cursors[0].executions[2][1] == (100, 11)


@pytest.mark.asyncio
async def test_model_limit_ends_the_nested_loop_with_an_exhausted_outcome() -> None:
    pool = FakePool()
    agent, model = _agent([sql_call(SAFE_SQL)], pool, policy=QueryAgentPolicy(model_call_limit=1))

    outcome = await _run(agent)

    assert outcome.status == "query_exhausted" and "nested_agent_limit_reached" in outcome.warnings
    assert model.calls == 1 and outcome.evidence == ()
