"""Checkpoint-backed history reads with opaque, endpoint-scoped keyset cursors."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from investigation_agent.application.invoke_turn import ThreadNotFound
from investigation_agent.application.thread_locks import ThreadLockRegistry
from investigation_agent.domain.history import Citation, HistoryMessage, HistoryRole, TurnStatus
from investigation_agent.domain.investigation_state import InvestigationState
from investigation_agent.ports.checkpoints import ThreadStateReader
from investigation_agent.ports.investigator import Investigator

type CursorValue = str | int


class InvalidCursor(RuntimeError):
    code = "invalid_cursor"
    public_message = "The pagination cursor is invalid."
    retryable = False


class CursorCodec:
    """Versioned opaque keyset cursor bound to one endpoint; tampering fails validation."""

    def __init__(self, *, max_token_chars: int = 2_048) -> None:
        if max_token_chars < 64:
            raise ValueError("cursor token bound is too small")
        self._max_token_chars = max_token_chars

    def encode(self, *, endpoint: str, position: Mapping[str, CursorValue]) -> str:
        payload = json.dumps(
            {"v": 1, "endpoint": endpoint, "position": dict(position)},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        token = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
        if len(token) > self._max_token_chars:
            raise InvalidCursor
        return token

    def decode(self, token: str, *, endpoint: str) -> dict[str, CursorValue]:
        if not token or len(token) > self._max_token_chars:
            raise InvalidCursor
        try:
            padding = "=" * (-len(token) % 4)
            raw = json.loads(base64.b64decode(token + padding, altchars=b"-_", validate=True))
        except (ValueError, UnicodeDecodeError) as exc:
            raise InvalidCursor from exc
        if not isinstance(raw, dict) or raw.get("v") != 1 or raw.get("endpoint") != endpoint:
            raise InvalidCursor
        position = raw.get("position")
        if not isinstance(position, dict) or any(
            not isinstance(key, str) or not isinstance(value, str | int) or isinstance(value, bool)
            for key, value in position.items()
        ):
            raise InvalidCursor
        return position


class ThreadSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    thread_id: str
    turn_id: str
    status: TurnStatus
    created_at: datetime


class ThreadPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[ThreadSummary, ...]
    next_cursor: str | None = None


class MessageItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    message_id: str
    sequence: int = Field(ge=1)
    turn_id: str
    request_id: str
    role: HistoryRole
    content: str
    citations: tuple[Citation, ...]
    turn_status: TurnStatus
    created_at: datetime


class MessagePage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[MessageItem, ...]
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class HistoryReadPolicy:
    default_page_size: int
    max_page_size: int
    max_checkpoint_scan: int = 10_000

    def __post_init__(self) -> None:
        if not 1 <= self.default_page_size <= self.max_page_size:
            raise ValueError("default history page size must be within the maximum")
        if self.max_checkpoint_scan < self.max_page_size:
            raise ValueError("checkpoint scan bound must cover one maximum page")


class ReadHistory:
    """Read only product transcript fields through the public framework APIs."""

    def __init__(
        self,
        *,
        investigator: Investigator,
        store: ThreadStateReader,
        locks: ThreadLockRegistry,
        cursors: CursorCodec,
        policy: HistoryReadPolicy,
    ) -> None:
        self._investigator = investigator
        self._store = store
        self._locks = locks
        self._cursors = cursors
        self._policy = policy

    async def list_threads(
        self, *, cursor: str | None = None, page_size: int | None = None
    ) -> ThreadPage:
        size = self._bounded_size(page_size)
        after = self._decode_thread_cursor(cursor)
        newest: dict[str, tuple[datetime, InvestigationState]] = {}
        records = self._store.scan_threads(limit=self._policy.max_checkpoint_scan)
        async for record in records:
            if record.state.turn is None:
                continue
            current = newest.get(record.thread_id)
            if current is None or record.checkpoint_at > current[0]:
                newest[record.thread_id] = (record.checkpoint_at, record.state)

        summaries = [
            await self._thread_summary(thread_id=thread_id, state=state)
            for thread_id, (_, state) in newest.items()
        ]
        summaries.sort(key=lambda item: (item.created_at, item.thread_id), reverse=True)
        if after is not None:
            summaries = [item for item in summaries if (item.created_at, item.thread_id) < after]
        selected = tuple(summaries[:size])
        next_cursor = None
        if len(summaries) > size and selected:
            last = selected[-1]
            next_cursor = self._cursors.encode(
                endpoint="threads",
                position={"created_at": last.created_at.isoformat(), "thread_id": last.thread_id},
            )
        return ThreadPage(items=selected, next_cursor=next_cursor)

    async def read_messages(
        self, *, thread_id: str, cursor: str | None = None, page_size: int | None = None
    ) -> MessagePage:
        size = self._bounded_size(page_size)
        endpoint = f"thread-messages:{thread_id}"
        after = self._decode_message_cursor(cursor, endpoint=endpoint)
        state = await self._load_state(thread_id)
        interrupted_turn_id = None
        if (
            state.turn is not None
            and state.turn.status is TurnStatus.RUNNING
            and not await self._locks.is_locked(thread_id)
        ):
            interrupted_turn_id = state.turn.turn_id
        messages = sorted(state.history.messages, key=lambda item: (item.sequence, item.message_id))
        if after is not None:
            messages = [item for item in messages if (item.sequence, item.message_id) > after]
        selected = messages[:size]
        items = tuple(
            _message_item(message, interrupted_turn_id=interrupted_turn_id) for message in selected
        )
        next_cursor = None
        if len(messages) > size and selected:
            last = selected[-1]
            next_cursor = self._cursors.encode(
                endpoint=endpoint,
                position={"sequence": last.sequence, "message_id": last.message_id},
            )
        return MessagePage(items=items, next_cursor=next_cursor)

    async def _load_state(self, thread_id: str) -> InvestigationState:
        state = await self._investigator.load_state(thread_id)
        if state is None:
            raise ThreadNotFound
        return state

    async def _thread_summary(self, *, thread_id: str, state: InvestigationState) -> ThreadSummary:
        turn = state.turn
        if turn is None:
            raise ValueError("thread state has no turn")
        status = turn.status
        if status is TurnStatus.RUNNING and not await self._locks.is_locked(thread_id):
            status = TurnStatus.INTERRUPTED
        created_at = min(
            (message.created_at for message in state.history.messages), default=turn.opened_at
        )
        return ThreadSummary(
            thread_id=thread_id,
            turn_id=turn.turn_id,
            status=status,
            created_at=created_at,
        )

    def _bounded_size(self, requested: int | None) -> int:
        if requested is None:
            return self._policy.default_page_size
        if requested < 1:
            raise ValueError("page_size must be positive")
        return min(requested, self._policy.max_page_size)

    def _decode_thread_cursor(self, cursor: str | None) -> tuple[datetime, str] | None:
        if cursor is None:
            return None
        position = self._cursors.decode(cursor, endpoint="threads")
        created_at = position.get("created_at")
        thread_id = position.get("thread_id")
        if not isinstance(created_at, str) or not isinstance(thread_id, str):
            raise InvalidCursor
        try:
            timestamp = datetime.fromisoformat(created_at)
        except ValueError as exc:
            raise InvalidCursor from exc
        if timestamp.tzinfo is None:
            raise InvalidCursor
        return timestamp, thread_id

    def _decode_message_cursor(
        self, cursor: str | None, *, endpoint: str
    ) -> tuple[int, str] | None:
        if cursor is None:
            return None
        position = self._cursors.decode(cursor, endpoint=endpoint)
        sequence = position.get("sequence")
        message_id = position.get("message_id")
        if not isinstance(sequence, int) or sequence < 1 or not isinstance(message_id, str):
            raise InvalidCursor
        return sequence, message_id


def _message_item(message: HistoryMessage, *, interrupted_turn_id: str | None) -> MessageItem:
    status = (
        TurnStatus.INTERRUPTED
        if message.turn_id == interrupted_turn_id and message.turn_status is TurnStatus.RUNNING
        else message.turn_status
    )
    return MessageItem(
        message_id=message.message_id,
        sequence=message.sequence,
        turn_id=message.turn_id,
        request_id=message.request_id,
        role=message.role,
        content=message.content,
        citations=message.citations,
        turn_status=status,
        created_at=message.created_at,
    )


__all__ = [
    "CursorCodec",
    "HistoryReadPolicy",
    "InvalidCursor",
    "MessageItem",
    "MessagePage",
    "ReadHistory",
    "ThreadPage",
    "ThreadSummary",
]
