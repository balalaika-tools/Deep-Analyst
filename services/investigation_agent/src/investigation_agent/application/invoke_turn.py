"""Resolve idempotency and serialization before one agent turn starts."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from investigation_agent.application.thread_locks import (
    ThreadAlreadyLockedError,
    ThreadLease,
    ThreadLockRegistry,
)
from investigation_agent.core.context import RuntimeContext
from investigation_agent.core.errors import IncompatibleStateFailure, translate_adapter_error
from investigation_agent.domain.history import (
    HistoryRole,
    TurnStatus,
    latest_turn_status,
    stable_message_id,
    stable_turn_id,
)
from investigation_agent.domain.investigation_state import (
    ControlState,
    IncompatibleStateError,
    InvestigationState,
    TurnState,
    new_turn_state,
    parse_state,
    state_update,
)
from investigation_agent.domain.tool_outcome import canonical_fingerprint

_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
APP_METADATA = "investigation"


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


@runtime_checkable
class GraphSnapshot(Protocol):
    @property
    def values(self) -> Mapping[str, Any]: ...


@runtime_checkable
class InvocationGraph(Protocol):
    async def aget_state(self, config: Mapping[str, object]) -> GraphSnapshot: ...

    def astream(
        self,
        input: Mapping[str, Any] | None,
        config: Mapping[str, object],
        *,
        context: RuntimeContext,
        stream_mode: list[str],
        durability: str,
        version: str,
    ) -> AsyncIterator[object]: ...


@dataclass(slots=True, eq=False)
class CancellationController:
    """Thread-safe cooperative cancellation shared across transport and graph work."""

    _event: threading.Event

    @classmethod
    def create(cls) -> CancellationController:
        return cls(threading.Event())

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    def check(self) -> None:
        if self.cancelled:
            raise asyncio.CancelledError


@dataclass(frozen=True, slots=True)
class InvocationPolicy:
    policy_version: str
    max_message_chars: int
    turn_timeout_s: float
    max_history_turns: int
    recursion_limit: int = 200

    def __post_init__(self) -> None:
        if not self.policy_version or self.max_message_chars < 1 or self.turn_timeout_s <= 0:
            raise ValueError("invocation policy values must be positive and non-empty")
        if self.max_history_turns < 1 or self.recursion_limit < 1:
            raise ValueError("history and recursion bounds must be positive")


class AttemptTelemetryLike(Protocol):
    """The subset of attempt telemetry the transport drives; ``None`` disables tracing."""

    def trace_carrier(self) -> dict[str, str]: ...

    def record_first_safe_progress(self) -> None: ...

    def record_answer_ready(self) -> None: ...

    def record_first_public_delta(self) -> None: ...

    def finish(self, *, outcome: str = "success") -> None: ...

    def fail(self, exc: BaseException | None, *, failure_class: Any = "internal") -> None: ...

    def cancel(self) -> None: ...

    def trace_stream(self, source: Any) -> Any: ...

    def activate(self) -> Any: ...

    @property
    def closed(self) -> bool: ...


class AttemptTelemetryFactoryLike(Protocol):
    def create(
        self,
        *,
        thread_id: str,
        turn_id: str,
        attempt: int,
        prior_trace_carrier: Mapping[str, str] | None,
        api_started_at: float | None = None,
    ) -> AttemptTelemetryLike: ...


@dataclass(slots=True)
class PreparedTurn:
    """A prepared turn whose lease is held until the response stream closes."""

    kind: PreparedTurnKind
    thread_id: str
    turn_id: str
    request_id: str
    graph: InvocationGraph
    config: Mapping[str, object]
    context: RuntimeContext
    graph_input: Mapping[str, Any] | None
    lease: ThreadLease
    turn_timeout_s: float
    replay_state: InvestigationState | None = None
    telemetry: AttemptTelemetryLike | None = None

    @property
    def cancellation(self) -> CancellationController:
        signal = self.context.cancellation
        if not isinstance(signal, CancellationController):
            raise TypeError("prepared turn cancellation signal is not controllable")
        return signal

    async def graph_events(self) -> AsyncIterator[object]:
        if self.kind in {PreparedTurnKind.REPLAY_COMPLETED, PreparedTurnKind.REPLAY_FAILED}:
            return
        events = self.graph.astream(
            self.graph_input,
            self.config,
            context=self.context,
            stream_mode=["updates", "custom"],
            durability="sync",
            version="v2",
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
        state = parse_state((await self.graph.aget_state(self.config)).values)
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
        graph: InvocationGraph,
        locks: ThreadLockRegistry,
        policy: InvocationPolicy,
        clock: Callable[[], datetime] | None = None,
        telemetry: AttemptTelemetryFactoryLike | None = None,
    ) -> None:
        self._graph = graph
        self._locks = locks
        self._policy = policy
        self._clock = clock or (lambda: datetime.now(UTC))
        self._telemetry = telemetry
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
        config = graph_config(
            thread_id=request.thread_id,
            recursion_limit=self._policy.recursion_limit,
        )
        try:
            state = parse_state((await self._graph.aget_state(config)).values)
        except IncompatibleStateError:
            raise IncompatibleStateFailure from None
        except Exception as exc:
            raise translate_adapter_error(exc) from None
        kind, graph_input, replay_state = self._resolve_action(state=state, request=request)
        turn_id = stable_turn_id(request.thread_id, request.request_id)
        telemetry = self._attempt_telemetry(kind, state=state, request=request, turn_id=turn_id)
        if telemetry is not None and graph_input is not None:
            graph_input = _with_trace_carrier(graph_input, telemetry.trace_carrier())
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
            graph=self._graph,
            config=config,
            context=context,
            graph_input=graph_input,
            lease=lease_with_cleanup,  # type: ignore[arg-type]
            turn_timeout_s=self._policy.turn_timeout_s,
            replay_state=replay_state,
            telemetry=telemetry,
        )

    def _attempt_telemetry(
        self,
        kind: PreparedTurnKind,
        *,
        state: InvestigationState | None,
        request: InvokeRequest,
        turn_id: str,
    ) -> AttemptTelemetryLike | None:
        """One finite root per agent invocation; a resume links to the prior attempt's context."""

        if self._telemetry is None or kind not in (PreparedTurnKind.NEW, PreparedTurnKind.RESUME):
            return None
        prior: dict[str, str] | None = None
        attempt = 1
        if kind is PreparedTurnKind.RESUME and state is not None and state.turn is not None:
            prior = dict(state.turn.prior_trace_carrier) or None
            attempt = 2
        return self._telemetry.create(
            thread_id=request.thread_id,
            turn_id=turn_id,
            attempt=attempt,
            prior_trace_carrier=prior,
            api_started_at=time.perf_counter(),
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


def _with_trace_carrier(
    graph_input: Mapping[str, Any], carrier: Mapping[str, str]
) -> dict[str, Any]:
    payload = dict(graph_input)
    turn = dict(payload.get("turn") or {})
    turn["prior_trace_carrier"] = [[key, value] for key, value in sorted(carrier.items())]
    payload["turn"] = turn
    return payload


def request_fingerprint(request: InvokeRequest) -> str:
    return canonical_fingerprint(
        {
            "version": 2,
            "request_id": request.request_id,
            "message": request.message,
        }
    )


def graph_config(*, thread_id: str, recursion_limit: int = 200) -> dict[str, Any]:
    """Public thread ID is the saver thread ID; metadata supports thread listing."""

    return {
        "configurable": {"thread_id": thread_id},
        "metadata": {"app": APP_METADATA, "public_thread_id": thread_id},
        "recursion_limit": recursion_limit,
    }


__all__ = [
    "APP_METADATA",
    "AttemptTelemetryFactoryLike",
    "AttemptTelemetryLike",
    "CancellationController",
    "GraphSnapshot",
    "IdempotencyConflict",
    "InvocationConflict",
    "InvocationGraph",
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
    "graph_config",
    "request_fingerprint",
    "stable_message_id",
    "stable_turn_id",
]
