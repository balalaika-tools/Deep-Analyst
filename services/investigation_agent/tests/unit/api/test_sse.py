from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from investigation_agent.api.sse import UPDATE_PHASES, heartbeat, stream_prepared_turn
from investigation_agent.application.invoke_turn import InvocationPolicy, InvokeRequest, InvokeTurn
from investigation_agent.application.thread_locks import ThreadLockRegistry
from investigation_agent.core.context import RuntimeContext
from investigation_agent.domain.history import (
    TurnStatus,
    append_assistant_message,
    append_user_message,
    stable_assistant_message_id,
)
from investigation_agent.domain.investigation_state import (
    InvestigationState,
    TurnState,
    parse_state,
)
from investigation_agent.genai.investigation.agent import EXPECTED_NODE_NAMES

NOW = datetime(2026, 3, 4, 5, 6, tzinfo=UTC)


@dataclass(frozen=True)
class Snapshot:
    values: Mapping[str, Any]


@dataclass
class StreamingGraph:
    values: dict[str, Any] | None = None
    answer: str = "Evidence-backed answer Δ"
    commit: bool = True
    error: BaseException | None = None
    stream_calls: int = 0
    raw_events: tuple[object, ...] = (
        {
            "type": "custom",
            "ns": (),
            "data": {
                "phase": "searching_evidence",
                "tool": "search_evidence",
                "attempt": 1,
                "query": "PRIVATE RETRIEVAL QUERY",
            },
        },
        {
            "type": "updates",
            "ns": (),
            "data": {"GroundingMiddleware.after_model": {"turn": "PRIVATE DRAFT"}},
        },
        {
            "type": "updates",
            "ns": (),
            "data": {"ModelCallLimitMiddleware.after_model": {"run_model_call_count": 1}},
        },
        {"type": "updates", "ns": (), "data": {"unknown_node": {"secret": "PRIVATE"}}},
        {"type": "messages", "data": "PRIVATE MODEL TOKEN"},
    )

    async def aget_state(self, config: Mapping[str, object]) -> Snapshot:
        del config
        return Snapshot(self.values or {})

    async def astream(
        self,
        input: Mapping[str, Any] | None,
        config: Mapping[str, object],
        *,
        context: RuntimeContext,
        stream_mode: list[str],
        durability: str,
        version: str,
    ) -> AsyncIterator[object]:
        del config, context
        self.stream_calls += 1
        assert stream_mode == ["updates", "custom"] and durability == "sync" and version == "v2"
        assert input is not None
        self.values = dict(input)
        for event in self.raw_events:
            yield event
        if self.error is not None:
            raise self.error
        if self.commit:
            self.values = _committed(input, answer=self.answer)


def _service(graph: StreamingGraph, locks: ThreadLockRegistry) -> InvokeTurn:
    return InvokeTurn(
        graph=graph,
        locks=locks,
        policy=InvocationPolicy(
            policy_version="policy-v1",
            max_message_chars=1_000,
            turn_timeout_s=30,
            max_history_turns=10,
        ),
        clock=lambda: NOW,
    )


def _request() -> InvokeRequest:
    return InvokeRequest(
        request_id="request-1",
        thread_id="thread-1",
        case_id="case-1",
        message="Investigate this account",
    )


def _committed(graph_input: Mapping[str, Any], *, answer: str) -> dict[str, Any]:
    state = parse_state(graph_input)
    assert state is not None and state.turn is not None
    turn = state.turn
    history = append_user_message(
        state.history,
        message_id=turn.user_message_id,
        turn_id=turn.turn_id,
        request_id=turn.request_id,
        content=turn.utterance,
        created_at=turn.opened_at,
        max_turns=10,
    )
    assistant_id = stable_assistant_message_id(turn.turn_id)
    history = append_assistant_message(
        history,
        message_id=assistant_id,
        turn_id=turn.turn_id,
        request_id=turn.request_id,
        content=answer,
        created_at=NOW,
    )
    committed = state.model_copy(
        update={
            "history": history,
            "turn": turn.model_copy(
                update={
                    "status": TurnStatus.COMPLETED,
                    "assistant_message_id": assistant_id,
                    "pending_answer": answer,
                }
            ),
        }
    )
    return committed.as_update()


async def _collect(graph: StreamingGraph) -> tuple[list[dict[str, object]], ThreadLockRegistry]:
    locks = ThreadLockRegistry()
    prepared = await _service(graph, locks).prepare(_request())
    encoded = [
        event async for event in stream_prepared_turn(prepared, chunk_chars=5, clock=lambda: NOW)
    ]
    return [json.loads(event["data"]) for event in encoded], locks


def test_update_allowlist_only_names_real_agent_nodes() -> None:
    assert set(UPDATE_PHASES) <= EXPECTED_NODE_NAMES
    assert "tools" not in UPDATE_PHASES


@pytest.mark.asyncio
async def test_only_committed_answer_is_sliced_and_private_graph_payloads_are_dropped() -> None:
    events, locks = await _collect(StreamingGraph())

    assert events[0]["event"] == "run.started"
    assert events[-1]["event"] == "run.completed"
    assert sum(event["event"] in {"run.completed", "run.failed"} for event in events) == 1
    deltas = [
        cast(str, cast(dict[str, object], event["data"])["text"])
        for event in events
        if event["event"] == "answer.delta"
    ]
    assert "".join(deltas) == "Evidence-backed answer Δ"
    serialized = json.dumps(events)
    assert "PRIVATE" not in serialized
    progress = [event["data"] for event in events if event["event"] == "progress"]
    assert progress == [
        {"phase": "searching_evidence", "tool": "search_evidence", "attempt": 1, "count": None},
    ]
    assert not await locks.is_locked("thread-1")


@pytest.mark.asyncio
async def test_uncommitted_state_emits_no_answer_and_retryable_persistence_failure() -> None:
    events, _ = await _collect(StreamingGraph(commit=False))

    assert all(event["event"] != "answer.delta" for event in events)
    assert events[-1]["event"] == "run.failed"
    assert events[-1]["data"] == {
        "code": "persistence_failed",
        "message": "The result could not be durably confirmed.",
        "retryable": True,
    }


@pytest.mark.asyncio
async def test_internal_graph_error_never_serializes_exception_text() -> None:
    events, _ = await _collect(
        StreamingGraph(error=RuntimeError("postgres host, generated SQL, and secret=abc"))
    )

    assert events[-1]["event"] == "run.failed"
    assert cast(dict[str, object], events[-1]["data"])["code"] == "internal"
    assert "postgres" not in json.dumps(events).lower()
    assert all(event["event"] != "answer.delta" for event in events)


@pytest.mark.asyncio
async def test_graph_timeout_is_reported_as_execution_budget_exhaustion() -> None:
    events, _ = await _collect(StreamingGraph(error=TimeoutError()))

    assert events[-1]["event"] == "run.failed"
    assert events[-1]["data"] == {
        "code": "budget_exhausted",
        "message": "The investigation reached a configured execution limit.",
        "retryable": False,
    }


@pytest.mark.asyncio
async def test_disconnect_cancels_graph_and_releases_lock_without_terminal_event() -> None:
    graph = StreamingGraph()
    locks = ThreadLockRegistry()
    prepared = await _service(graph, locks).prepare(_request())
    probes = 0

    async def disconnected() -> bool:
        nonlocal probes
        probes += 1
        return probes > 1

    events = [
        json.loads(event["data"])
        async for event in stream_prepared_turn(
            prepared, chunk_chars=5, disconnected=disconnected, clock=lambda: NOW
        )
    ]

    assert [event["event"] for event in events] == ["run.started"]
    assert prepared.cancellation.cancelled
    assert not await locks.is_locked("thread-1")


@pytest.mark.asyncio
async def test_durably_failed_replay_is_terminal_and_not_retryable() -> None:
    graph = StreamingGraph()
    locks = ThreadLockRegistry()
    service = _service(graph, locks)
    initial = await service.prepare(_request())
    assert initial.graph_input is not None
    state = parse_state(initial.graph_input)
    assert state is not None and state.turn is not None
    turn = state.turn
    history = append_user_message(
        state.history,
        message_id=turn.user_message_id,
        turn_id=turn.turn_id,
        request_id=turn.request_id,
        content=turn.utterance,
        created_at=turn.opened_at,
        max_turns=10,
    )
    failed_turn: TurnState = turn.model_copy(
        update={
            "status": TurnStatus.FAILED,
            "safe_failure_code": "transient_exhausted",
            "intake_complete": True,
        }
    )
    graph.values = InvestigationState(
        control=state.control, turn=failed_turn, history=history
    ).as_update()
    await initial.close()

    replay = await service.prepare(_request())
    events = [
        json.loads(event["data"])
        async for event in stream_prepared_turn(replay, chunk_chars=5, clock=lambda: NOW)
    ]

    assert [event["event"] for event in events] == ["run.started", "run.failed"]
    assert events[-1]["data"] == {
        "code": "transient_exhausted",
        "message": "Temporary operation attempts were exhausted.",
        "retryable": False,
    }
    assert graph.stream_calls == 0


def test_heartbeat_is_an_sse_comment_not_a_public_event() -> None:
    assert heartbeat().encode() == b": heartbeat\r\n\r\n"
