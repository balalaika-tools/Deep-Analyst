from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from investigation_agent.application.invoke_turn import ThreadNotFound
from investigation_agent.application.read_history import (
    CursorCodec,
    HistoryReadPolicy,
    InvalidCursor,
    ReadHistory,
)
from investigation_agent.application.thread_locks import ThreadLockRegistry
from investigation_agent.domain.history import (
    HistoryState,
    TurnStatus,
    append_assistant_message,
    append_user_message,
    stable_message_id,
    stable_turn_id,
)
from investigation_agent.domain.investigation_state import (
    ControlState,
    InvestigationState,
    new_turn_state,
)
from investigation_agent.ports.checkpoints import StoredThreadState

NOW = datetime(2026, 2, 1, 12, tzinfo=UTC)


@dataclass
class HistoryGraph:
    state: InvestigationState | None
    threads: list[str] = field(default_factory=list)

    async def load_state(self, thread_id: str) -> InvestigationState | None:
        self.threads.append(thread_id)
        return self.state

    async def stream_turn(
        self,
        turn_input: Any,
        *,
        thread_id: str,
        context: Any,
        max_steps: int,
    ) -> AsyncIterator[object]:
        del turn_input, thread_id, context, max_steps
        if False:
            yield None


@dataclass
class FakeCheckpointer:
    records: list[StoredThreadState]
    limits: list[int | None] = field(default_factory=list)

    def scan_threads(self, *, limit: int) -> AsyncIterator[StoredThreadState]:
        self.limits.append(limit)

        async def _records() -> AsyncIterator[StoredThreadState]:
            for record in self.records[:limit]:
                yield record

        return _records()


def _running_history_state() -> InvestigationState:
    history = HistoryState()
    turn_1 = stable_turn_id("thread-1", "request-1")
    history = append_user_message(
        history,
        message_id=stable_message_id(turn_1),
        turn_id=turn_1,
        request_id="request-1",
        content="First exact user message",
        created_at=NOW,
        max_turns=10,
    )
    history = append_assistant_message(
        history,
        message_id="assistant-1",
        turn_id=turn_1,
        request_id="request-1",
        content="First committed answer",
        created_at=NOW + timedelta(seconds=1),
    )
    turn_2 = stable_turn_id("thread-1", "request-2")
    user_2 = stable_message_id(turn_2)
    history = append_user_message(
        history,
        message_id=user_2,
        turn_id=turn_2,
        request_id="request-2",
        content="Phone +30 210 000 0000 and account 77",
        created_at=NOW + timedelta(minutes=1),
        max_turns=10,
    )
    turn = new_turn_state(
        turn_id=turn_2,
        request_id="request-2",
        message_id=user_2,
        utterance="Phone +30 210 000 0000 and account 77",
        opened_at=NOW + timedelta(minutes=1),
    )
    return InvestigationState(
        control=ControlState(policy_version="policy-v1"),
        turn=turn,
        history=history,
    )


def _thread(
    *, thread_id: str, request_id: str, created_at: datetime, status: TurnStatus
) -> InvestigationState:
    turn_id = stable_turn_id(thread_id, request_id)
    message_id = stable_message_id(turn_id)
    history = append_user_message(
        HistoryState(),
        message_id=message_id,
        turn_id=turn_id,
        request_id=request_id,
        content="Question",
        created_at=created_at,
        max_turns=10,
    )
    turn = new_turn_state(
        turn_id=turn_id,
        request_id=request_id,
        message_id=message_id,
        utterance="Question",
        opened_at=created_at,
    ).model_copy(update={"status": status})
    if status is TurnStatus.COMPLETED:
        history = append_assistant_message(
            history,
            message_id=f"assistant-{request_id}",
            turn_id=turn_id,
            request_id=request_id,
            content="Answer",
            created_at=created_at + timedelta(seconds=1),
        )
        turn = turn.model_copy(update={"assistant_message_id": f"assistant-{request_id}"})
    return InvestigationState(
        control=ControlState(policy_version="policy-v1"),
        turn=turn,
        history=history,
    )


def _reader(
    graph: HistoryGraph,
    *,
    checkpointer: FakeCheckpointer | None = None,
    locks: ThreadLockRegistry | None = None,
) -> ReadHistory:
    return ReadHistory(
        investigator=graph,
        store=checkpointer or FakeCheckpointer([]),
        locks=locks or ThreadLockRegistry(),
        cursors=CursorCodec(),
        policy=HistoryReadPolicy(default_page_size=2, max_page_size=3, max_checkpoint_scan=20),
    )


def _record(
    thread_id: str,
    state: InvestigationState,
    *,
    checkpoint_at: datetime,
    app: str = "investigation",
) -> StoredThreadState:
    del app
    return StoredThreadState(
        thread_id=thread_id,
        checkpoint_at=checkpoint_at,
        state=state,
    )


def test_cursor_is_opaque_endpoint_scoped_and_rejects_tampering() -> None:
    codec = CursorCodec()
    token = codec.encode(
        endpoint="threads", position={"created_at": NOW.isoformat(), "thread_id": "thread-1"}
    )

    assert codec.decode(token, endpoint="threads")["thread_id"] == "thread-1"
    with pytest.raises(InvalidCursor):
        codec.decode(f"{token[:-3]}!!!", endpoint="threads")
    with pytest.raises(InvalidCursor):
        codec.decode(token, endpoint="thread-messages:thread-1")
    assert "owner" not in token.lower()


@pytest.mark.asyncio
async def test_message_cursor_remains_continuous_when_assistant_is_appended() -> None:
    state = _running_history_state()
    graph = HistoryGraph(state)
    reader = _reader(graph)

    first = await reader.read_messages(thread_id="thread-1", page_size=2)
    assert [item.sequence for item in first.items] == [1, 2]
    assert first.next_cursor is not None
    assert {item.turn_id for item in first.items} and all(item.turn_status for item in first.items)

    assert state.turn is not None
    completed_history = append_assistant_message(
        state.history,
        message_id="assistant-2",
        turn_id=state.turn.turn_id,
        request_id=state.turn.request_id,
        content="Second committed answer",
        created_at=NOW + timedelta(minutes=2),
    )
    graph.state = state.model_copy(
        update={
            "history": completed_history,
            "turn": state.turn.model_copy(
                update={"status": TurnStatus.COMPLETED, "assistant_message_id": "assistant-2"}
            ),
        }
    )
    second = await reader.read_messages(thread_id="thread-1", cursor=first.next_cursor, page_size=3)

    assert [item.sequence for item in second.items] == [3, 4]
    assert "Phone +30 210 000 0000" in second.items[0].content
    assert second.next_cursor is None
    assert graph.threads == ["thread-1", "thread-1"]


@pytest.mark.asyncio
async def test_unlocked_running_turn_is_exposed_as_interrupted_without_mutating_state() -> None:
    state = _running_history_state()

    page = await _reader(HistoryGraph(state)).read_messages(thread_id="thread-1", page_size=3)

    assert page.items[-1].turn_status is TurnStatus.INTERRUPTED
    assert state.history.messages[-1].turn_status is TurnStatus.RUNNING
    serialized = page.model_dump(mode="json")
    assert set(serialized["items"][0]) == {
        "message_id",
        "sequence",
        "turn_id",
        "request_id",
        "role",
        "content",
        "citations",
        "turn_status",
        "created_at",
    }


@pytest.mark.asyncio
async def test_locked_running_turn_reads_as_running_and_unknown_thread_is_not_found() -> None:
    state = _running_history_state()
    locks = ThreadLockRegistry()
    lease = await locks.try_acquire(thread_id="thread-1", request_id="request-2")
    page = await _reader(HistoryGraph(state), locks=locks).read_messages(
        thread_id="thread-1", page_size=3
    )
    assert page.items[-1].turn_status is TurnStatus.RUNNING
    await lease.release()

    with pytest.raises(ThreadNotFound):
        await _reader(HistoryGraph(None)).read_messages(thread_id="thread-1")


@pytest.mark.asyncio
async def test_thread_list_uses_app_filter_keeps_newest_checkpoint_and_pages_stably() -> None:
    old_a = _thread(
        thread_id="thread-a", request_id="request-a1", created_at=NOW, status=TurnStatus.COMPLETED
    )
    newest_a = _thread(
        thread_id="thread-a", request_id="request-a2", created_at=NOW, status=TurnStatus.RUNNING
    )
    thread_b = _thread(
        thread_id="thread-b",
        request_id="request-b1",
        created_at=NOW + timedelta(days=1),
        status=TurnStatus.COMPLETED,
    )
    checkpointer = FakeCheckpointer(
        [
            _record("thread-a", old_a, checkpoint_at=NOW + timedelta(hours=1)),
            _record("thread-b", thread_b, checkpoint_at=NOW + timedelta(days=2)),
            _record("thread-a", newest_a, checkpoint_at=NOW + timedelta(days=4)),
        ]
    )
    reader = _reader(HistoryGraph(None), checkpointer=checkpointer)

    first = await reader.list_threads(page_size=1)
    second = await reader.list_threads(cursor=first.next_cursor, page_size=1)

    assert [(item.thread_id, item.status) for item in first.items] == [
        ("thread-b", TurnStatus.COMPLETED)
    ]
    assert [(item.thread_id, item.status) for item in second.items] == [
        ("thread-a", TurnStatus.INTERRUPTED)
    ]
    assert second.next_cursor is None
    assert checkpointer.limits == [20, 20]
    assert set(first.model_dump(mode="json")["items"][0]) == {
        "thread_id",
        "turn_id",
        "status",
        "created_at",
    }
