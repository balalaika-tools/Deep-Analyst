"""Bounded product transcript kept in checkpoints but never supplied to a model."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated

from evidence_model import SourceRef
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator


class TurnStatus(StrEnum):
    RUNNING = "running"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"


class HistoryRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class Citation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: Annotated[str, Field(min_length=1, max_length=256)]
    content_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    source_ref: SourceRef


class HistoryMessage(BaseModel):
    """One exact accepted user message or committed assistant message."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    message_id: Annotated[str, Field(min_length=1, max_length=128)]
    sequence: PositiveInt
    turn_id: Annotated[str, Field(min_length=1, max_length=128)]
    request_id: Annotated[str, Field(min_length=1, max_length=128)]
    role: HistoryRole
    content: Annotated[str, Field(min_length=1, max_length=128_000)]
    citations: Annotated[tuple[Citation, ...], Field(max_length=256)] = ()
    turn_status: TurnStatus
    created_at: datetime

    @model_validator(mode="after")
    def _citations_belong_to_assistant(self) -> HistoryMessage:
        if self.role is HistoryRole.USER and self.citations:
            raise ValueError("user messages cannot carry citations")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return self


class HistoryState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    messages: tuple[HistoryMessage, ...] = ()
    next_sequence: PositiveInt = 1


class ThreadFullError(RuntimeError):
    code = "thread_full"


class HistoryInvariantError(ValueError):
    pass


def append_user_message(
    history: HistoryState,
    *,
    message_id: str,
    turn_id: str,
    request_id: str,
    content: str,
    created_at: datetime | None = None,
    max_turns: int,
) -> HistoryState:
    """Append the one user message that opens a turn, rejecting duplicates and full threads."""

    if any(message.request_id == request_id for message in history.messages):
        return history
    existing_turns = {message.turn_id for message in history.messages}
    if turn_id not in existing_turns and len(existing_turns) >= max_turns:
        raise ThreadFullError("the configured thread turn limit has been reached")
    message = HistoryMessage(
        message_id=message_id,
        sequence=history.next_sequence,
        turn_id=turn_id,
        request_id=request_id,
        role=HistoryRole.USER,
        content=content,
        turn_status=TurnStatus.RUNNING,
        created_at=created_at or datetime.now(UTC),
    )
    return HistoryState(
        messages=(*history.messages, message), next_sequence=history.next_sequence + 1
    )


def append_assistant_message(
    history: HistoryState,
    *,
    message_id: str,
    turn_id: str,
    request_id: str,
    content: str,
    citations: tuple[Citation, ...] = (),
    status: TurnStatus = TurnStatus.COMPLETED,
    created_at: datetime | None = None,
) -> HistoryState:
    """Commit one terminal assistant message and update every message owned by the turn."""

    if status not in (TurnStatus.COMPLETED, TurnStatus.FAILED):
        raise HistoryInvariantError("assistant messages require a terminal turn status")
    existing = [message for message in history.messages if message.turn_id == turn_id]
    if not existing or existing[0].request_id != request_id:
        raise HistoryInvariantError("assistant message must match an existing user turn")
    if any(message.role is HistoryRole.ASSISTANT for message in existing):
        return history
    updated = tuple(
        message.model_copy(update={"turn_status": status})
        if message.turn_id == turn_id
        else message
        for message in history.messages
    )
    assistant = HistoryMessage(
        message_id=message_id,
        sequence=history.next_sequence,
        turn_id=turn_id,
        request_id=request_id,
        role=HistoryRole.ASSISTANT,
        content=content,
        citations=citations,
        turn_status=status,
        created_at=created_at or datetime.now(UTC),
    )
    return HistoryState(messages=(*updated, assistant), next_sequence=history.next_sequence + 1)


def set_turn_status(history: HistoryState, turn_id: str, status: TurnStatus) -> HistoryState:
    if not any(message.turn_id == turn_id for message in history.messages):
        raise HistoryInvariantError("turn does not exist in history")
    messages = tuple(
        message.model_copy(update={"turn_status": status})
        if message.turn_id == turn_id
        else message
        for message in history.messages
    )
    return history.model_copy(update={"messages": messages})


def stable_turn_id(public_thread_id: str, request_id: str) -> str:
    digest = hashlib.sha256(f"turn\x00{public_thread_id}\x00{request_id}".encode()).hexdigest()
    return f"turn_{digest[:32]}"


def stable_message_id(turn_id: str) -> str:
    digest = hashlib.sha256(f"user-message\x00{turn_id}".encode()).hexdigest()
    return f"message_{digest[:32]}"


def stable_assistant_message_id(turn_id: str) -> str:
    digest = hashlib.sha256(f"assistant-message\x00{turn_id}".encode()).hexdigest()
    return f"message_{digest[:32]}"


def latest_turn_status(history: HistoryState, turn_id: str) -> TurnStatus | None:
    for message in reversed(history.messages):
        if message.turn_id == turn_id:
            return message.turn_status
    return None


__all__ = [
    "Citation",
    "HistoryInvariantError",
    "HistoryMessage",
    "HistoryRole",
    "HistoryState",
    "ThreadFullError",
    "TurnStatus",
    "append_assistant_message",
    "append_user_message",
    "latest_turn_status",
    "set_turn_status",
    "stable_assistant_message_id",
    "stable_message_id",
    "stable_turn_id",
]
