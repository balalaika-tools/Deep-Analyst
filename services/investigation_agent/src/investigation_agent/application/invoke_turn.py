"""Resolve idempotency and serialization before one agent turn starts."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from investigation_agent.application.thread_locks import (
    ThreadAlreadyLockedError,
    ThreadLease,
    ThreadLockRegistry,
)
from investigation_agent.core.context import CancellationController, RuntimeContext
from investigation_agent.domain.history import (
    HistoryRole,
    TurnStatus,
    latest_turn_status,
    stable_message_id,
    stable_turn_id,
)
from investigation_agent.domain.investigation_state import (
    ControlState,
    InvestigationState,
    TurnState,
    new_turn_state,
    state_update,
)
from investigation_agent.domain.tool_outcome import canonical_fingerprint
from investigation_agent.ports.investigator import Investigator

_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"


class InvokeRequest(BaseModel):
    """Public invocation fields; there is no caller identity in this prototype."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(pattern=_ID_PATTERN)
    thread_id: str = Field(pattern=_ID_PATTERN)
    message: str = Field(min_length=1, max_length=64_000)

    @field_validator("message")
    @classmethod
    def _message_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must contain non-whitespace text")
        return value


class PreparedTurnKind(StrEnum):
    NEW = "new"
    RESUME = "resume"
    REPLAY_COMPLETED = "replay_completed"
    REPLAY_FAILED = "replay_failed"


class InvocationConflict(RuntimeError):
    code = "conflict"
    public_message = "The request conflicts with the current thread state."
    retryable = False


class RequestInProgress(InvocationConflict):
    code = "request_in_progress"
    public_message = "This request is already in progress."
    retryable = True


class ThreadBusy(InvocationConflict):
    code = "thread_busy"
    public_message = "Another request is already running for this thread."
    retryable = True


class IdempotencyConflict(InvocationConflict):
    code = "idempotency_conflict"
    public_message = "The request ID was already used with different content."


class ThreadFull(InvocationConflict):
    code = "thread_full"
    public_message = "This thread cannot accept another turn."


class ThreadNotFound(RuntimeError):
    code = "resource_not_found"
    public_message = "The requested resource is not available."
    retryable = False


class MessageTooLarge(RuntimeError):
    code = "invalid_request"
    public_message = "The request or configuration is invalid."
    retryable = False


@dataclass(frozen=True, slots=True)
class InvocationPolicy:
    policy_version: str
    max_message_chars: int
    turn_timeout_s: float
    max_history_turns: int
    max_agent_steps: int = 200

    def __post_init__(self) -> None:
        if not self.policy_version or self.max_message_chars < 1 or self.turn_timeout_s <= 0:
            raise ValueError("invocation policy values must be positive and non-empty")
        if self.max_history_turns < 1 or self.max_agent_steps < 1:
            raise ValueError("history and agent-step bounds must be positive")


@dataclass(slots=True)
class PreparedTurn:
    """A prepared turn whose lease is held until the response stream closes."""

    kind: PreparedTurnKind
    thread_id: str
    turn_id: str
    request_id: str
    investigator: Investigator
    context: RuntimeContext
    graph_input: Mapping[str, Any] | None
    lease: ThreadLease
    turn_timeout_s: float
    max_steps: int
    attempt: int
    prior_trace_carrier: Mapping[str, str] | None
    replay_state: InvestigationState | None = None

    @property
    def cancellation(self) -> CancellationController:
        signal = self.context.cancellation
        if not isinstance(signal, CancellationController):
            raise TypeError("prepared turn cancellation signal is not controllable")
        return signal

    async def graph_events(self) -> AsyncIterator[object]:
        if self.kind in {PreparedTurnKind.REPLAY_COMPLETED, PreparedTurnKind.REPLAY_FAILED}:
            return
        events = self.investigator.stream_turn(
            self.graph_input,
            thread_id=self.thread_id,
            context=self.context,
            max_steps=self.max_steps,
        ).__aiter__()
        deadline = asyncio.get_running_loop().time() + self.turn_timeout_s
        while True:
            # The budget wraps only the await: a scope spanning ``yield`` would expire while the
            # consumer is suspended elsewhere and surface as a bare CancelledError instead.
            try:
                async with asyncio.timeout_at(deadline):
                    event = await anext(events)
            except StopAsyncIteration:
                return
            yield event

    async def latest_state(self) -> InvestigationState:
        if self.replay_state is not None:
            return self.replay_state
        state = await self.investigator.load_state(self.thread_id)
        if state is None:
            raise RuntimeError("checkpoint state is unavailable")
        return state

    async def close(self) -> None:
        await self.lease.release()


class InvokeTurn:
    """Application action that completes every risky decision before SSE starts."""

    def __init__(
        self,
        *,
        investigator: Investigator,
        locks: ThreadLockRegistry,
        policy: InvocationPolicy,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._investigator = investigator
        self._locks = locks
        self._policy = policy
        self._clock = clock or (lambda: datetime.now(UTC))
        self._active: set[CancellationController] = set()

    async def prepare(self, request: InvokeRequest) -> PreparedTurn:
        if len(request.message) > self._policy.max_message_chars:
            raise MessageTooLarge
        try:
            lease = await self._locks.try_acquire(
                thread_id=request.thread_id, request_id=request.request_id
            )
        except ThreadAlreadyLockedError as exc:
            if exc.active_request_id == request.request_id:
                raise RequestInProgress from None
            raise ThreadBusy from None
        try:
            return await self._prepare_locked(request=request, lease=lease)
        except BaseException:
            await lease.release()
            raise

    async def cancel_active(self) -> None:
        """Cooperatively cancel every executing turn; used by the bounded shutdown drain."""

        for controller in list(self._active):
            controller.cancel()

    @property
    def active_count(self) -> int:
        return len(self._active)

    async def _prepare_locked(self, *, request: InvokeRequest, lease: ThreadLease) -> PreparedTurn:
        state = await self._investigator.load_state(request.thread_id)
        kind, graph_input, replay_state = self._resolve_action(state=state, request=request)
        turn_id = stable_turn_id(request.thread_id, request.request_id)
        attempt, prior_trace_carrier = _attempt_link(kind, state)
        cancellation = CancellationController.create()
        context = RuntimeContext(
            thread_id=request.thread_id,
            request_id=request.request_id,
            deadline=self._clock() + timedelta(seconds=self._policy.turn_timeout_s),
            cancellation=cancellation,
        )
        self._active.add(cancellation)
        lease_with_cleanup = _CleanupLease(lease, lambda: self._active.discard(cancellation))
        return PreparedTurn(
            kind=kind,
            thread_id=request.thread_id,
            turn_id=turn_id,
            request_id=request.request_id,
            investigator=self._investigator,
            context=context,
            graph_input=graph_input,
            lease=lease_with_cleanup,  # type: ignore[arg-type]
            turn_timeout_s=self._policy.turn_timeout_s,
            max_steps=self._policy.max_agent_steps,
            attempt=attempt,
            prior_trace_carrier=prior_trace_carrier,
            replay_state=replay_state,
        )

    def _resolve_action(
        self, *, state: InvestigationState | None, request: InvokeRequest
    ) -> tuple[PreparedTurnKind, Mapping[str, Any] | None, InvestigationState | None]:
        if state is None:
            return PreparedTurnKind.NEW, self._new_turn_input(request, state=None), None
        turn = state.turn
        if turn is not None and turn.request_id == request.request_id:
            if turn.request_fingerprint != request_fingerprint(request):
                raise IdempotencyConflict
            if turn.status is TurnStatus.COMPLETED:
                return PreparedTurnKind.REPLAY_COMPLETED, None, state
            if turn.status is TurnStatus.FAILED:
                return PreparedTurnKind.REPLAY_FAILED, None, state
            return PreparedTurnKind.RESUME, None, None
        prior = _prior_turn_from_history(state, request)
        if prior is not None:
            kind = (
                PreparedTurnKind.REPLAY_COMPLETED
                if prior.status is TurnStatus.COMPLETED
                else PreparedTurnKind.REPLAY_FAILED
            )
            return kind, None, state.model_copy(update={"turn": prior})
        if self._thread_is_full(state):
            raise ThreadFull
        return PreparedTurnKind.NEW, self._new_turn_input(request, state=state), None

    def _thread_is_full(self, state: InvestigationState) -> bool:
        turns = {message.turn_id for message in state.history.messages}
        return len(turns) >= self._policy.max_history_turns

    def _new_turn_input(
        self, request: InvokeRequest, *, state: InvestigationState | None
    ) -> Mapping[str, Any]:
        turn_id = stable_turn_id(request.thread_id, request.request_id)
        turn = new_turn_state(
            turn_id=turn_id,
            request_id=request.request_id,
            message_id=stable_message_id(turn_id),
            utterance=request.message,
            opened_at=self._clock(),
        )
        payload: dict[str, Any] = {
            "messages": [{"role": "user", "content": request.message, "id": turn.user_message_id}],
        }
        if state is None:
            payload.update(
                state_update(
                    control=ControlState(policy_version=self._policy.policy_version),
                    turn=turn,
                )
            )
        else:
            payload.update(state_update(turn=turn))
        return payload


class _CleanupLease:
    """Release the thread lease and drop the turn from the active set exactly once."""

    def __init__(self, lease: ThreadLease, cleanup: Callable[[], None]) -> None:
        self._lease = lease
        self._cleanup = cleanup

    @property
    def thread_id(self) -> str:
        return self._lease.thread_id

    @property
    def request_id(self) -> str:
        return self._lease.request_id

    async def release(self) -> None:
        self._cleanup()
        await self._lease.release()


def _prior_turn_from_history(state: InvestigationState, request: InvokeRequest) -> TurnState | None:
    """Rebuild the turn view of an older request so it replays instead of starting a new turn.

    The transcript stores no fingerprint, so the comparison reduces to the accepted user message
    text. Only the current turn can still be running; any other non-completed turn is terminal for
    its caller.
    """

    messages = [m for m in state.history.messages if m.request_id == request.request_id]
    user = next((m for m in messages if m.role is HistoryRole.USER), None)
    if user is None:
        return None
    if user.content != request.message:
        raise IdempotencyConflict
    assistant = next((m for m in messages if m.role is HistoryRole.ASSISTANT), None)
    completed = latest_turn_status(state.history, user.turn_id) is TurnStatus.COMPLETED
    turn = new_turn_state(
        turn_id=user.turn_id,
        request_id=request.request_id,
        message_id=user.message_id,
        utterance=user.content,
        opened_at=user.created_at,
    )
    return turn.model_copy(
        update={
            "status": TurnStatus.COMPLETED if completed else TurnStatus.FAILED,
            "assistant_message_id": assistant.message_id if assistant is not None else None,
            "intake_complete": True,
        }
    )


def _attempt_link(
    kind: PreparedTurnKind, state: InvestigationState | None
) -> tuple[int, Mapping[str, str] | None]:
    if kind is PreparedTurnKind.NEW:
        return 1, None
    if kind is PreparedTurnKind.RESUME and state is not None and state.turn is not None:
        return 2, dict(state.turn.prior_trace_carrier) or None
    return 0, None


def request_fingerprint(request: InvokeRequest) -> str:
    return canonical_fingerprint(
        {
            "version": 2,
            "request_id": request.request_id,
            "message": request.message,
        }
    )


__all__ = [
    "CancellationController",
    "IdempotencyConflict",
    "InvocationConflict",
    "InvocationPolicy",
    "InvokeRequest",
    "InvokeTurn",
    "MessageTooLarge",
    "PreparedTurn",
    "PreparedTurnKind",
    "RequestInProgress",
    "ThreadBusy",
    "ThreadFull",
    "ThreadNotFound",
    "request_fingerprint",
    "stable_message_id",
    "stable_turn_id",
]
