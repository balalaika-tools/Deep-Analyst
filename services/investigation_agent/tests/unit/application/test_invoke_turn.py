from __future__ import annotations

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
    ThreadCaseConflict,
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


def _request(
    *, request_id: str = "request-1", message: str = "Find the transfer", case_id: str = "case-1"
) -> InvokeRequest:
    return InvokeRequest(
        request_id=request_id, thread_id="thread-1", case_id=case_id, message=message
    )


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
    request: InvokeRequest, *, case_id: str = "case-1", status: TurnStatus = TurnStatus.RUNNING
) -> InvestigationState:
    turn_id = stable_turn_id(request.thread_id, request.request_id)
    message_id = stable_message_id(turn_id)
    turn = new_turn_state(
        turn_id=turn_id,
        request_id=request.request_id,
        message_id=message_id,
        utterance=request.message,
        case_id=case_id,
        opened_at=NOW,
    ).model_copy(update={"status": status, "intake_complete": True})
    history = append_user_message(
        InvestigationState(
            control=ControlState(case_id=case_id, policy_version="policy-v1")
        ).history,
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
        control=ControlState(case_id=case_id, policy_version="policy-v1"),
        turn=turn,
        history=history,
    )


@pytest.mark.asyncio
async def test_new_thread_binds_the_case_and_uses_the_public_thread_id_for_the_saver() -> None:
    graph = FakeGraph()
    request = InvokeRequest.model_validate({**_request().model_dump(), "owner_id": "attacker"})

    prepared = await _service(graph).prepare(request)

    assert prepared.kind is PreparedTurnKind.NEW
    assert prepared.config["configurable"] == {"thread_id": "thread-1"}
    assert prepared.config["metadata"] == {
        "app": "investigation",
        "public_thread_id": "thread-1",
        "case_id": "case-1",
    }
    assert prepared.graph_input is not None
    assert prepared.graph_input["control"]["case_id"] == "case-1"
    assert prepared.graph_input["turn"]["utterance"] == "Find the transfer"
    assert prepared.graph_input["messages"][0]["content"] == "Find the transfer"
    assert prepared.context.case_id == "case-1" and prepared.context.thread_id == "thread-1"
    await prepared.close()


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
async def test_case_rebind_full_thread_and_incompatible_state_are_rejected_before_execution() -> (
    None
):
    request = _request()
    with pytest.raises(ThreadCaseConflict):
        await _service(FakeGraph(_state(request, case_id="case-2"))).prepare(request)

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
