"""Stable SSE translation over private LangGraph v2 update and custom streams."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sse_starlette import ServerSentEvent

from investigation_agent.api.problems import PublicFailure, public_failure, public_failure_for_code
from investigation_agent.application.invoke_turn import PreparedTurn, PreparedTurnKind
from investigation_agent.domain.history import Citation, HistoryMessage, HistoryRole, TurnStatus
from investigation_agent.domain.investigation_state import InvestigationState


class PublicEvent(StrEnum):
    RUN_STARTED = "run.started"
    PROGRESS = "progress"
    ANSWER_DELTA = "answer.delta"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"


class ProgressPhase(StrEnum):
    CHECKING_SCOPE = "checking_scope"
    UPDATING_CONTEXT = "updating_context"
    PLANNING = "planning"
    SEARCHING_EVIDENCE = "searching_evidence"
    QUERYING_RECORDS = "querying_records"
    FINDING_CONNECTIONS = "finding_connections"
    VERIFYING_ANSWER = "verifying_answer"
    COMMITTING_ANSWER = "committing_answer"


class PublicTool(StrEnum):
    SEARCH_EVIDENCE = "search_evidence"
    QUERY_RECORDS = "query_records"
    FIND_CONNECTIONS = "find_connections"


class StartedData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["running"] = "running"


class ProgressData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    phase: ProgressPhase
    tool: PublicTool | None = None
    attempt: int | None = Field(default=None, ge=1, le=1_000)
    count: int | None = Field(default=None, ge=0, le=1_000_000)


class AnswerDeltaData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    index: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=16_384)


class CompletedData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    message_id: str
    citations: tuple[Citation, ...]
    status: Literal["completed"] = "completed"


class FailedData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=512)
    retryable: bool


type PublicEventData = StartedData | ProgressData | AnswerDeltaData | CompletedData | FailedData


class SseEnvelope(BaseModel):
    """Public event shape independent of LangGraph payload structure."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    event: PublicEvent
    thread_id: str
    turn_id: str
    timestamp: datetime
    data: PublicEventData


type DisconnectProbe = Callable[[], Awaitable[bool]]
type Clock = Callable[[], datetime]

# ``create_agent`` node names (model, tools, and each middleware hook node) mapped to the coarse
# public phases. Unknown node names emit nothing; the tools node is covered by tool progress.
UPDATE_PHASES: dict[str, ProgressPhase] = {
    "TurnIntakeMiddleware.before_agent": ProgressPhase.UPDATING_CONTEXT,
}


async def stream_prepared_turn(
    prepared: PreparedTurn,
    *,
    chunk_chars: int,
    disconnected: DisconnectProbe | None = None,
    clock: Clock | None = None,
) -> AsyncIterator[dict[str, str]]:
    """Emit one start and, while connected, exactly one sanitized terminal event."""

    if not 1 <= chunk_chars <= 16_384:
        raise ValueError("SSE chunk size must contain 1-16384 characters")
    disconnect_probe = disconnected or _connected
    now = clock or (lambda: datetime.now(UTC))
    telemetry = prepared.telemetry
    terminal_emitted = False
    try:
        if await disconnect_probe():
            prepared.cancellation.cancel()
            _close_telemetry(telemetry, outcome="cancelled")
            return
        yield _encoded(_envelope(prepared, PublicEvent.RUN_STARTED, StartedData(), clock=now))

        try:
            async for raw_event in _traced_events(prepared):
                if await disconnect_probe():
                    prepared.cancellation.cancel()
                    _close_telemetry(telemetry, outcome="cancelled")
                    return
                progress = _progress_from_graph_event(raw_event)
                if progress is not None:
                    if telemetry is not None:
                        telemetry.record_first_safe_progress()
                    yield _encoded(_envelope(prepared, PublicEvent.PROGRESS, progress, clock=now))
        except asyncio.CancelledError:
            if not _cooperatively_cancelled(prepared):
                prepared.cancellation.cancel()
                _close_telemetry(telemetry, outcome="cancelled")
                raise
            _close_telemetry(telemetry, outcome="cancelled")
            if not await disconnect_probe():
                terminal_emitted = True
                yield _encoded(
                    _failed_envelope(prepared, public_failure_for_code("cancelled"), clock=now)
                )
            return
        except Exception as exc:
            cancelled = prepared.cancellation.cancelled
            timed_out = isinstance(exc, TimeoutError)
            _close_telemetry(
                telemetry,
                outcome="cancelled" if cancelled else "error",
                exc=exc,
                failure_class="budget" if timed_out else "internal",
            )
            if not await disconnect_probe():
                terminal_emitted = True
                failure = (
                    public_failure_for_code("cancelled")
                    if cancelled
                    else public_failure_for_code("budget_exhausted")
                    if timed_out
                    else public_failure(exc)
                )
                yield _encoded(_failed_envelope(prepared, failure, clock=now))
            return

        if await disconnect_probe():
            prepared.cancellation.cancel()
            _close_telemetry(telemetry, outcome="cancelled")
            return
        try:
            state = await prepared.latest_state()
        except Exception as exc:
            terminal_emitted = True
            _close_telemetry(telemetry, outcome="error", exc=exc, failure_class="dependency")
            yield _encoded(
                _failed_envelope(prepared, public_failure_for_code("persistence_failed"), clock=now)
            )
            return
        if telemetry is not None and state.turn is not None:
            if state.turn.status is TurnStatus.COMPLETED:
                telemetry.record_answer_ready()

        async for event in _terminal_events(
            prepared,
            state=state,
            chunk_chars=chunk_chars,
            disconnected=disconnect_probe,
            clock=now,
            telemetry=telemetry,
        ):
            if event["event"] in {PublicEvent.RUN_COMPLETED.value, PublicEvent.RUN_FAILED.value}:
                terminal_emitted = True
            yield event
        _close_telemetry(
            telemetry,
            outcome=_terminal_outcome(state, terminal_emitted=terminal_emitted),
            failure_class=_terminal_failure_class(state),
        )
    except asyncio.CancelledError:
        prepared.cancellation.cancel()
        _close_telemetry(telemetry, outcome="cancelled")
        raise
    except Exception as exc:
        _close_telemetry(telemetry, outcome="error", exc=exc, failure_class="internal")
        if not terminal_emitted and not await disconnect_probe():
            yield _encoded(
                _failed_envelope(prepared, public_failure_for_code("delivery_failed"), clock=now)
            )
    finally:
        _close_telemetry(telemetry, outcome="cancelled")
        await prepared.close()


async def _traced_events(prepared: PreparedTurn) -> AsyncIterator[object]:
    """Run graph events under the attempt root without leaving it current across yields."""

    telemetry = prepared.telemetry
    source = prepared.graph_events()
    if telemetry is None:
        async for event in source:
            yield event
        return
    iterator = source.__aiter__()
    while True:
        try:
            with telemetry.activate():
                event = await anext(iterator)
        except StopAsyncIteration:
            return
        yield event


def _cooperatively_cancelled(prepared: PreparedTurn) -> bool:
    """Distinguish the shutdown drain's cooperative cancel from the transport cancelling us.

    The controller raises CancelledError from inside graph work while the streaming task itself
    is not being cancelled, so the client is still connected and owed a terminal event.
    """

    if not prepared.cancellation.cancelled:
        return False
    task = asyncio.current_task()
    return task is None or task.cancelling() == 0


# Durable ``safe_failure_code`` values folded onto the telemetry failure taxonomy.
_FAILURE_CLASSES: dict[str, str] = {
    "invalid_request": "validation",
    "conflict": "conflict",
    "thread_full": "conflict",
    "policy_rejected": "policy",
    "no_support": "no_support",
    "no_retrieved_support": "no_support",
    "retrieval_incomplete": "no_support",
    "grounding_failed": "no_support",
    "transient_exhausted": "transient_exhaustion",
    "budget_exhausted": "budget",
    "cancelled": "cancelled",
    "dependency_unavailable": "dependency",
    "guardrail_unavailable": "dependency",
    "persistence_failed": "dependency",
    "delivery_failed": "dependency",
    "incompatible_state": "incompatible_state",
}


def _terminal_failure_class(state: InvestigationState) -> str:
    turn = state.turn
    if turn is None or turn.status is not TurnStatus.FAILED:
        return "internal"
    return _FAILURE_CLASSES.get(turn.safe_failure_code or "", "internal")


def _terminal_outcome(state: InvestigationState, *, terminal_emitted: bool) -> str:
    turn = state.turn
    if turn is None or not terminal_emitted:
        return "cancelled"
    if turn.status is TurnStatus.COMPLETED:
        return "refused" if turn.answer_kind == "refusal" else "success"
    if turn.safe_failure_code == "budget_exhausted":
        return "budget_exhausted"
    return "error"


def _close_telemetry(
    telemetry: Any,
    *,
    outcome: str,
    exc: BaseException | None = None,
    failure_class: str = "internal",
) -> None:
    """Close the attempt root exactly once with the correct status; never raise."""

    if telemetry is None or telemetry.closed:
        return
    try:
        if outcome == "cancelled":
            telemetry.cancel()
        elif outcome == "error":
            telemetry.fail(exc, failure_class=failure_class)
        else:
            telemetry.finish(outcome=outcome)
    except Exception:
        return


async def _terminal_events(
    prepared: PreparedTurn,
    *,
    state: InvestigationState,
    chunk_chars: int,
    disconnected: DisconnectProbe,
    clock: Clock,
    telemetry: Any = None,
) -> AsyncIterator[dict[str, str]]:
    turn = state.turn
    if turn is None or turn.turn_id != prepared.turn_id:
        yield _encoded(
            _failed_envelope(prepared, public_failure_for_code("persistence_failed"), clock=clock)
        )
        return
    if turn.status is TurnStatus.FAILED:
        failure = public_failure_for_code(turn.safe_failure_code)
        if prepared.kind is PreparedTurnKind.REPLAY_FAILED:
            failure = replace(failure, retryable=False)
        yield _encoded(_failed_envelope(prepared, failure, clock=clock))
        return
    if turn.status is not TurnStatus.COMPLETED:
        yield _encoded(
            _failed_envelope(prepared, public_failure_for_code("persistence_failed"), clock=clock)
        )
        return

    message = _committed_message(state, prepared=prepared)
    if message is None:
        yield _encoded(
            _failed_envelope(prepared, public_failure_for_code("persistence_failed"), clock=clock)
        )
        return
    for index, start in enumerate(range(0, len(message.content), chunk_chars)):
        if await disconnected():
            prepared.cancellation.cancel()
            return
        if telemetry is not None and index == 0:
            telemetry.record_first_public_delta()
        text = message.content[start : start + chunk_chars]
        yield _encoded(
            _envelope(
                prepared,
                PublicEvent.ANSWER_DELTA,
                AnswerDeltaData(index=index, text=text),
                clock=clock,
            )
        )
    if await disconnected():
        prepared.cancellation.cancel()
        return
    yield _encoded(
        _envelope(
            prepared,
            PublicEvent.RUN_COMPLETED,
            CompletedData(message_id=message.message_id, citations=message.citations),
            clock=clock,
        )
    )


def _committed_message(
    state: InvestigationState, *, prepared: PreparedTurn
) -> HistoryMessage | None:
    turn = state.turn
    if turn is None or turn.assistant_message_id is None:
        return None
    matches = [
        message
        for message in state.history.messages
        if message.message_id == turn.assistant_message_id
        and message.turn_id == prepared.turn_id
        and message.request_id == prepared.request_id
        and message.role is HistoryRole.ASSISTANT
        and message.turn_status is TurnStatus.COMPLETED
    ]
    return matches[0] if len(matches) == 1 else None


def _progress_from_graph_event(raw_event: object) -> ProgressData | None:
    if not isinstance(raw_event, Mapping):
        return None
    event_type = raw_event.get("type")
    data = raw_event.get("data")
    if event_type == "updates" and isinstance(data, Mapping):
        for node_name in data:
            phase = UPDATE_PHASES.get(str(node_name))
            if phase is not None:
                return ProgressData(phase=phase)
        return None
    if event_type != "custom" or not isinstance(data, Mapping):
        return None
    try:
        return ProgressData.model_validate(
            {
                "phase": data.get("phase"),
                "tool": data.get("tool"),
                "attempt": data.get("attempt"),
                "count": data.get("count"),
            }
        )
    except ValidationError:
        return None


def _failed_envelope(
    prepared: PreparedTurn, failure: PublicFailure, *, clock: Clock
) -> SseEnvelope:
    return _envelope(
        prepared,
        PublicEvent.RUN_FAILED,
        FailedData(code=failure.code, message=failure.message, retryable=failure.retryable),
        clock=clock,
    )


def _envelope(
    prepared: PreparedTurn, event: PublicEvent, data: PublicEventData, *, clock: Clock
) -> SseEnvelope:
    timestamp = clock()
    if timestamp.tzinfo is None:
        raise ValueError("SSE timestamps must be timezone-aware")
    return SseEnvelope(
        event=event,
        thread_id=prepared.thread_id,
        turn_id=prepared.turn_id,
        timestamp=timestamp,
        data=data,
    )


def _encoded(envelope: SseEnvelope) -> dict[str, str]:
    return {"event": envelope.event.value, "data": envelope.model_dump_json()}


async def _connected() -> bool:
    return False


def heartbeat() -> ServerSentEvent:
    return ServerSentEvent(comment="heartbeat")


__all__ = [
    "UPDATE_PHASES",
    "AnswerDeltaData",
    "CompletedData",
    "FailedData",
    "ProgressData",
    "ProgressPhase",
    "PublicEvent",
    "PublicTool",
    "SseEnvelope",
    "StartedData",
    "heartbeat",
    "stream_prepared_turn",
]
