from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from investigation_agent.application.invoke_turn import (
    IdempotencyConflict,
    InvocationPolicy,
    InvokeRequest,
    InvokeTurn,
    PreparedTurnKind,
    RequestInProgress,
    ThreadBusy,
    ThreadFull,
)
from investigation_agent.application.thread_locks import ThreadLockRegistry
from investigation_agent.core.context import RuntimeContext
from investigation_agent.core.errors import IncompatibleStateFailure
from investigation_agent.domain.history import (
    TurnStatus,
    append_assistant_message,
    append_user_message,
    set_turn_status,
    stable_message_id,
    stable_turn_id,
)
from investigation_agent.domain.investigation_state import (
    ControlState,
    InvestigationState,
    new_turn_state,
)
from pydantic import ValidationError

NOW = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)


@dataclass(frozen=True)
class FakeSnapshot:
    values: Mapping[str, Any]


@dataclass
class FakeGraph:
    state: InvestigationState | None = None
    raw_values: Mapping[str, Any] | None = None
    get_calls: list[Mapping[str, object]] = field(default_factory=list)
    stream_calls: list[Mapping[str, Any] | None] = field(default_factory=list)

    async def aget_state(self, config: Mapping[str, object]) -> FakeSnapshot:
        self.get_calls.append(config)
        if self.raw_values is not None:
            return FakeSnapshot(self.raw_values)
        return FakeSnapshot(self.state.as_update() if self.state else {})

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
        del config, context, stream_mode, durability, version
        self.stream_calls.append(input)
        if False:
            yield None


def _request(*, request_id: str = "request-1", message: str = "Find the transfer") -> InvokeRequest:
    return InvokeRequest(request_id=request_id, thread_id="thread-1", message=message)


def _service(
    graph: FakeGraph, *, locks: ThreadLockRegistry | None = None, max_turns: int = 10
) -> InvokeTurn:
    return InvokeTurn(
        graph=graph,
        locks=locks or ThreadLockRegistry(),
        policy=InvocationPolicy(
            policy_version="policy-v1",
            max_message_chars=1_000,
            turn_timeout_s=30,
            max_history_turns=max_turns,
        ),
        clock=lambda: NOW,
    )


def _state(
    request: InvokeRequest, *, status: TurnStatus = TurnStatus.RUNNING
) -> InvestigationState:
    turn_id = stable_turn_id(request.thread_id, request.request_id)
    message_id = stable_message_id(turn_id)
    turn = new_turn_state(
        turn_id=turn_id,
        request_id=request.request_id,
        message_id=message_id,
        utterance=request.message,
        opened_at=NOW,
    ).model_copy(update={"status": status, "intake_complete": True})
    history = append_user_message(
        InvestigationState(control=ControlState(policy_version="policy-v1")).history,
        message_id=message_id,
        turn_id=turn_id,
        request_id=request.request_id,
        content=request.message,
        created_at=NOW,
        max_turns=10,
    )
    if status is TurnStatus.COMPLETED:
        history = append_assistant_message(
            history,
            message_id="assistant-1",
            turn_id=turn_id,
            request_id=request.request_id,
            content="Committed answer",
            created_at=NOW,
        )
        turn = turn.model_copy(update={"assistant_message_id": "assistant-1"})
    elif status is TurnStatus.FAILED:
        history = set_turn_status(history, turn_id, TurnStatus.FAILED)
        turn = turn.model_copy(update={"safe_failure_code": "budget_exhausted"})
    return InvestigationState(
        control=ControlState(policy_version="policy-v1"),
        turn=turn,
        history=history,
    )


@pytest.mark.asyncio
async def test_new_thread_uses_the_public_thread_id_for_the_saver() -> None:
    graph = FakeGraph()
    request = _request()

    prepared = await _service(graph).prepare(request)

    assert prepared.kind is PreparedTurnKind.NEW
    assert prepared.config["configurable"] == {"thread_id": "thread-1"}
    assert prepared.config["metadata"] == {
        "app": "investigation",
        "public_thread_id": "thread-1",
    }
    assert prepared.graph_input is not None
    assert prepared.graph_input["turn"]["utterance"] == "Find the transfer"
    assert prepared.graph_input["messages"][0]["content"] == "Find the transfer"
    assert prepared.context.thread_id == "thread-1"
    await prepared.close()


def test_removed_scope_field_is_rejected() -> None:
    removed_field = "case" + "_id"
    with pytest.raises(ValidationError):
        InvokeRequest.model_validate({**_request().model_dump(), removed_field: "legacy"})


@pytest.mark.asyncio
async def test_live_thread_rejects_same_and_different_requests_without_second_executor() -> None:
    graph = FakeGraph()
    locks = ThreadLockRegistry()
    service = _service(graph, locks=locks)
    first = await service.prepare(_request())

    with pytest.raises(RequestInProgress):
        await service.prepare(_request())
    with pytest.raises(ThreadBusy):
        await service.prepare(_request(request_id="request-2"))

    assert len(graph.get_calls) == 1
    assert service.active_count == 1
    await first.close()
    assert service.active_count == 0
    assert not await locks.is_locked("thread-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "kind"),
    [
        pytest.param(TurnStatus.COMPLETED, PreparedTurnKind.REPLAY_COMPLETED, id="completed"),
        pytest.param(TurnStatus.FAILED, PreparedTurnKind.REPLAY_FAILED, id="failed"),
    ],
)
async def test_terminal_retry_is_prepared_as_replay(
    status: TurnStatus, kind: PreparedTurnKind
) -> None:
    request = _request()
    graph = FakeGraph(_state(request, status=status))
    prepared = await _service(graph).prepare(request)

    assert prepared.kind is kind
    assert prepared.graph_input is None
    assert prepared.replay_state == graph.state
    assert [event async for event in prepared.graph_events()] == []
    assert graph.stream_calls == []
    await prepared.close()


@pytest.mark.asyncio
async def test_same_request_id_with_byte_different_message_conflicts_and_releases_lock() -> None:
    original = _request(message="Account  7")
    graph = FakeGraph(_state(original))
    locks = ThreadLockRegistry()

    with pytest.raises(IdempotencyConflict):
        await _service(graph, locks=locks).prepare(_request(message="Account 7"))

    assert not await locks.is_locked("thread-1")


@pytest.mark.asyncio
async def test_unlocked_running_request_resumes_from_checkpoint_with_none_input() -> None:
    request = _request()
    graph = FakeGraph(_state(request, status=TurnStatus.RUNNING))

    prepared = await _service(graph).prepare(request)

    assert prepared.kind is PreparedTurnKind.RESUME
    assert prepared.graph_input is None
    await prepared.close()


@pytest.mark.asyncio
async def test_different_request_supersedes_unlocked_running_turn_with_turn_only_input() -> None:
    graph = FakeGraph(_state(_request(), status=TurnStatus.RUNNING))

    prepared = await _service(graph).prepare(
        _request(request_id="request-2", message="Continue with the beneficiary")
    )

    assert prepared.kind is PreparedTurnKind.NEW
    assert prepared.graph_input is not None
    assert "control" not in prepared.graph_input and "history" not in prepared.graph_input
    assert prepared.graph_input["turn"]["request_id"] == "request-2"
    await prepared.close()


@pytest.mark.asyncio
async def test_full_thread_and_incompatible_state_are_rejected_before_execution() -> None:
    request = _request()
    completed = _state(request, status=TurnStatus.COMPLETED)
    with pytest.raises(ThreadFull):
        await _service(FakeGraph(completed), max_turns=1).prepare(_request(request_id="request-2"))

    stale = {**completed.as_update()}
    stale["control"] = {**stale["control"], "state_schema_version": 1}
    with pytest.raises(IncompatibleStateFailure):
        await _service(FakeGraph(raw_values=stale)).prepare(request)


@pytest.mark.asyncio
async def test_cancel_active_marks_every_executing_turn_cancelled() -> None:
    graph = FakeGraph()
    service = _service(graph)
    prepared = await service.prepare(_request())

    await service.cancel_active()

    assert prepared.cancellation.cancelled
    await prepared.close()


def _two_turn_state(first: InvokeRequest, second: InvokeRequest) -> InvestigationState:
    """A thread whose current turn is ``second``; ``first`` is an older completed turn."""

    state = _state(first, status=TurnStatus.COMPLETED)
    second_turn_id = stable_turn_id(second.thread_id, second.request_id)
    history = append_user_message(
        state.history,
        message_id=stable_message_id(second_turn_id),
        turn_id=second_turn_id,
        request_id=second.request_id,
        content=second.message,
        created_at=NOW,
        max_turns=10,
    )
    history = append_assistant_message(
        history,
        message_id="assistant-2",
        turn_id=second_turn_id,
        request_id=second.request_id,
        content="Second answer",
        created_at=NOW,
    )
    turn = new_turn_state(
        turn_id=second_turn_id,
        request_id=second.request_id,
        message_id=stable_message_id(second_turn_id),
        utterance=second.message,
        opened_at=NOW,
    ).model_copy(update={"status": TurnStatus.COMPLETED, "assistant_message_id": "assistant-2"})
    return state.model_copy(update={"turn": turn, "history": history})


@pytest.mark.asyncio
async def test_older_completed_request_is_replayed_without_running_the_graph() -> None:
    first = _request()
    second = _request(request_id="request-2", message="Then the beneficiary")
    graph = FakeGraph(_two_turn_state(first, second))

    prepared = await _service(graph).prepare(first)

    assert prepared.kind is PreparedTurnKind.REPLAY_COMPLETED
    assert prepared.graph_input is None
    assert [event async for event in prepared.graph_events()] == []
    assert graph.stream_calls == []
    assert prepared.replay_state is not None and prepared.replay_state.turn is not None
    replayed = prepared.replay_state.turn
    assert replayed.turn_id == stable_turn_id(first.thread_id, first.request_id)
    assert replayed.assistant_message_id == "assistant-1"
    assert replayed.status is TurnStatus.COMPLETED
    await prepared.close()


@pytest.mark.asyncio
async def test_older_request_id_with_different_message_conflicts_instead_of_starting() -> None:
    first = _request()
    second = _request(request_id="request-2", message="Then the beneficiary")
    graph = FakeGraph(_two_turn_state(first, second))
    locks = ThreadLockRegistry()

    with pytest.raises(IdempotencyConflict):
        await _service(graph, locks=locks).prepare(_request(message="Find the transfer!"))

    assert graph.stream_calls == []
    assert not await locks.is_locked("thread-1")


@dataclass
class SlowGraph(FakeGraph):
    async def astream(self, input: Any, config: Any, **kwargs: Any) -> AsyncIterator[object]:
        del input, config, kwargs
        yield {"type": "custom", "data": {}}
        await asyncio.sleep(10)
        yield {"type": "custom", "data": {}}


@pytest.mark.asyncio
async def test_turn_timeout_surfaces_as_timeout_error_even_when_consumer_is_slow() -> None:
    service = InvokeTurn(
        graph=SlowGraph(),
        locks=ThreadLockRegistry(),
        policy=InvocationPolicy(
            policy_version="policy-v1",
            max_message_chars=1_000,
            turn_timeout_s=0.05,
            max_history_turns=10,
        ),
        clock=lambda: NOW,
    )
    prepared = await service.prepare(_request())
    events = prepared.graph_events()

    first = await anext(events)
    assert first == {"type": "custom", "data": {}}
    # The consumer holds the first event past the deadline; the budget must still be reported as
    # a timeout on the next pull rather than as a bare cancellation.
    await asyncio.sleep(0.1)
    with pytest.raises(TimeoutError):
        await anext(events)
    await prepared.close()
