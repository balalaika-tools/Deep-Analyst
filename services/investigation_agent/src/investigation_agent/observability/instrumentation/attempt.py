"""Finite attempt lifecycle and aggregate investigation telemetry."""

from __future__ import annotations

import asyncio
import time
import traceback
from collections.abc import AsyncIterable, AsyncIterator, Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypeVar

from observability import (
    ERROR_TYPE,
    GENAI_CATEGORY,
    TELEMETRY_CATEGORY,
    WORKFLOW_RUN_ID,
    get_logger,
    mark_failed,
)
from observability.genai_metrics import TokenUsage
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import Link, Span, SpanKind, Tracer
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.util.types import AttributeValue

from investigation_agent.observability.events import (
    ATTEMPT_SPAN_NAME,
    WORKFLOW_NAME,
    AttemptMeasurements,
    FailureClass,
    InvestigationInstruments,
    LogEvent,
    Outcome,
)
from investigation_agent.observability.instrumentation.context import (
    LogicalOperation,
    OperationKind,
    current_attempt_var,
    current_operation_var,
)

T = TypeVar("T")


class EventLogger(Protocol):
    def info(self, event: str, **fields: Any) -> Any: ...

    def error(self, event: str, **fields: Any) -> Any: ...


@contextmanager
def phase_span(name: str) -> Iterator[None]:
    """Open a bounded phase span under the current attempt, or nothing outside one."""

    attempt = current_attempt()
    if attempt is None or attempt.closed:
        yield
        return
    with attempt.phase(name):
        yield


@dataclass(frozen=True, slots=True)
class AttemptTelemetryFactory:
    """Create one finite attempt root per agent invocation from shared providers."""

    tracer: Tracer
    instruments: InvestigationInstruments

    def create(
        self,
        *,
        thread_id: str,
        turn_id: str,
        attempt: int,
        prior_trace_carrier: Mapping[str, str] | None,
        api_started_at: float | None = None,
    ) -> AttemptTelemetry:
        return AttemptTelemetry(
            tracer=self.tracer,
            instruments=self.instruments,
            workflow_run_id=f"{turn_id}:{attempt}",
            thread_id=thread_id,
            turn_id=turn_id,
            attempt=attempt,
            prior_trace_carrier=prior_trace_carrier,
            api_started_at=api_started_at,
        )


def current_attempt() -> AttemptTelemetry | None:
    """Return the attempt active for this task, if instrumentation was bound."""
    return current_attempt_var.get()


def _prior_link(carrier: Mapping[str, str] | None) -> Link | None:
    if not carrier:
        return None
    try:
        extracted = TraceContextTextMapPropagator().extract(carrier=dict(carrier))
        span_context = trace.get_current_span(extracted).get_span_context()
    except Exception:
        return None
    return Link(span_context) if span_context.is_valid else None


class AttemptTelemetry:
    """Own one new-root trace and one set of aggregate signals for a graph attempt."""

    def __init__(
        self,
        *,
        tracer: Tracer,
        instruments: InvestigationInstruments,
        workflow_run_id: str,
        thread_id: str,
        turn_id: str,
        attempt: int,
        prior_trace_carrier: Mapping[str, str] | None = None,
        api_started_at: float | None = None,
        logger: EventLogger | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._tracer = tracer
        self._instruments = instruments
        self._logger = logger or get_logger(__name__)
        self._clock = clock
        self._started_at = clock()
        self._api_started_at = api_started_at
        self.workflow_run_id = workflow_run_id
        self.thread_id = thread_id
        self.turn_id = turn_id
        self.attempt = attempt

        self._span = self._start_root(prior_trace_carrier)
        self._closed = False
        self._cancelled = False
        self._failure_logged = False
        self._logical_model_calls = 0
        self._logical_tool_calls = 0
        self._model_calls = 0
        self._tool_calls = 0
        self._input_tokens: int | None = None
        self._output_tokens: int | None = None
        self._result_counts: dict[str, int] = {}
        self._cleanups: dict[object, Callable[[], None]] = {}
        self._first_safe_progress_s: float | None = None
        self._answer_ready_s: float | None = None
        self._first_public_delta_s: float | None = None
        self._handled_exception: BaseException | None = None

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def instruments(self) -> InvestigationInstruments:
        """Return the attempt-scoped aggregate recorder used by model callbacks."""
        return self._instruments

    def _start_root(self, prior_trace_carrier: Mapping[str, str] | None) -> Span:
        attributes: dict[str, AttributeValue] = {
            TELEMETRY_CATEGORY: GENAI_CATEGORY,
            WORKFLOW_RUN_ID: self.workflow_run_id,
            "gen_ai.operation.name": "invoke_workflow",
            "gen_ai.workflow.name": WORKFLOW_NAME,
            "app.investigation.thread.id": self.thread_id,
            "app.investigation.turn.id": self.turn_id,
            "app.investigation.attempt": self.attempt,
        }
        link = _prior_link(prior_trace_carrier)
        try:
            return self._tracer.start_span(
                ATTEMPT_SPAN_NAME,
                context=Context(),
                kind=SpanKind.INTERNAL,
                attributes=attributes,
                links=[link] if link else None,
                record_exception=False,
                set_status_on_exception=False,
            )
        except Exception:
            return trace.INVALID_SPAN

    @contextmanager
    def activate(self) -> Iterator[AttemptTelemetry]:
        """Make the root and counters current only for the enclosed work slice."""
        attempt_token = current_attempt_var.set(self)
        try:
            with trace.use_span(
                self._span,
                end_on_exit=False,
                record_exception=False,
                set_status_on_exception=False,
            ):
                yield self
        finally:
            current_attempt_var.reset(attempt_token)

    @contextmanager
    def phase(self, name: str) -> Iterator[Span]:
        """Create a bounded child span for a real workflow phase, never for a delta."""
        with self._child_span(name, {}) as span:
            yield span

    @contextmanager
    def logical_operation(self, kind: OperationKind) -> Iterator[LogicalOperation]:
        """Bind one stable ID around retry middleware and all its physical attempts."""
        ordinal = self._increment_logical(kind)
        operation = LogicalOperation(
            kind=kind,
            operation_id=f"{self.workflow_run_id}:{kind}:{ordinal}",
        )
        token = current_operation_var.set(operation)
        attributes: dict[str, AttributeValue] = {
            "app.gen_ai.logical_operation.id": operation.operation_id,
            "app.gen_ai.operation.kind": kind,
        }
        try:
            with self._child_span(f"{kind}_operation", attributes):
                yield operation
        finally:
            current_operation_var.reset(token)

    @contextmanager
    def physical_tool_attempt(self, tool_name: str) -> Iterator[None]:
        """Trace one physical tool execution; register this inside retry middleware."""
        operation = current_operation_var.get()
        if operation is None or operation.kind != "tool":
            with self.logical_operation("tool"):
                with self.physical_tool_attempt(tool_name):
                    yield
            return
        operation.attempts += 1
        self._tool_calls += 1
        started_at = self._clock()
        error_type: str | None = None
        attributes: dict[str, AttributeValue] = {
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": tool_name,
            "gen_ai.tool.type": "function",
            "app.gen_ai.logical_operation.id": operation.operation_id,
            "app.gen_ai.physical_attempt": operation.attempts,
        }
        try:
            with self._child_span(f"execute_tool {tool_name}", attributes):
                yield
        except BaseException as exc:
            error_type = type(exc).__name__
            raise
        finally:
            self._safe_call(
                self._instruments.record_tool_execution,
                max(0.0, self._clock() - started_at),
                tool_name=tool_name,
                error_type=error_type,
            )

    @contextmanager
    def _child_span(
        self,
        name: str,
        attributes: Mapping[str, AttributeValue],
    ) -> Iterator[Span]:
        if self._closed:
            yield trace.INVALID_SPAN
            return
        classified = {**attributes, TELEMETRY_CATEGORY: GENAI_CATEGORY}
        try:
            span = self._tracer.start_span(
                name,
                attributes=classified,
                record_exception=False,
                set_status_on_exception=False,
            )
        except Exception:
            yield trace.INVALID_SPAN
            return
        try:
            with trace.use_span(
                span,
                end_on_exit=False,
                record_exception=False,
                set_status_on_exception=False,
            ):
                try:
                    yield span
                except BaseException as exc:
                    self._safe_call(mark_failed, span, type(exc).__name__)
                    raise
        finally:
            self._safe_call(span.end)

    def _increment_logical(self, kind: OperationKind) -> int:
        if kind == "model":
            self._logical_model_calls += 1
            return self._logical_model_calls
        self._logical_tool_calls += 1
        return self._logical_tool_calls

    def record_model_attempt(self) -> tuple[str | None, int]:
        """Count a callback-observed request and return its retry correlation."""
        operation = current_operation_var.get()
        if operation is None or operation.kind != "model":
            self._logical_model_calls += 1
            operation_id = None
            physical_attempt = 1
        else:
            operation.attempts += 1
            operation_id = operation.operation_id
            physical_attempt = operation.attempts
        self._model_calls += 1
        return operation_id, physical_attempt

    def record_token_usage(self, usage: TokenUsage | None) -> None:
        if usage is None:
            return
        if usage.input_tokens is not None:
            self._input_tokens = (self._input_tokens or 0) + usage.input_tokens
        if usage.output_tokens is not None:
            self._output_tokens = (self._output_tokens or 0) + usage.output_tokens

    def record_results(self, kind: str, count: int) -> None:
        if count < 0:
            raise ValueError("result count must be non-negative")
        self._result_counts[kind] = self._result_counts.get(kind, 0) + count

    def register_cleanup(self, owner: object, cleanup: Callable[[], None]) -> None:
        """Register one idempotent telemetry cleanup for this attempt."""
        self._cleanups.setdefault(owner, cleanup)

    def record_first_safe_progress(self) -> None:
        if self._first_safe_progress_s is None:
            self._first_safe_progress_s = self._elapsed()

    def record_answer_ready(self) -> None:
        if self._answer_ready_s is None:
            self._answer_ready_s = self._elapsed()

    def record_first_public_delta(self) -> None:
        if self._first_public_delta_s is None:
            self._first_public_delta_s = self._elapsed()

    def record_handled_failure(self, exc: BaseException) -> None:
        """Retain a content-safe diagnostic for a failure translated into graph state."""
        if self._handled_exception is None:
            self._handled_exception = exc

    def trace_carrier(self) -> dict[str, str]:
        """Return only W3C propagation fields suitable for checkpointed turn state."""
        carrier: dict[str, str] = {}
        try:
            context = trace.set_span_in_context(self._span, Context())
            TraceContextTextMapPropagator().inject(carrier, context=context)
        except Exception:
            return {}
        return carrier

    def ensure_not_cancelled(self) -> None:
        """Prevent middleware from starting another physical attempt after cancellation."""
        if self._cancelled:
            raise asyncio.CancelledError

    def finish(self, *, outcome: str = Outcome.SUCCESS) -> None:
        error_type: FailureClass | None = None
        if outcome == Outcome.BUDGET_EXHAUSTED:
            error_type = "budget"
        elif outcome == Outcome.REFUSED:
            error_type = "policy"
        self._close(outcome=outcome, error_type=error_type)

    def fail(
        self,
        exc: BaseException | None,
        *,
        failure_class: FailureClass = "internal",
    ) -> None:
        """Close as failed and emit one content-safe diagnostic stack when available."""
        self._close(
            outcome=Outcome.ERROR,
            error_type=failure_class,
            exception=exc or self._handled_exception,
        )

    def cancel(self) -> None:
        self._cancelled = True
        self._close(outcome=Outcome.CANCELLED, error_type="CancelledError")

    async def trace_stream(self, source: AsyncIterable[T]) -> AsyncIterator[T]:
        """Resume an async source under the root without leaving context current at yields."""
        iterator = source.__aiter__()
        try:
            while True:
                try:
                    with self.activate():
                        item = await anext(iterator)
                except StopAsyncIteration:
                    break
                yield item
        except asyncio.CancelledError:
            self.cancel()
            raise
        except GeneratorExit:
            self.cancel()
            raise
        except BaseException as exc:
            self.fail(exc)
            raise
        else:
            self.finish()

    def _close(
        self,
        *,
        outcome: str,
        error_type: FailureClass | Literal["CancelledError"] | None,
        exception: BaseException | None = None,
    ) -> None:
        if self._closed:
            return
        self._closed = True
        duration_s = self._elapsed()
        api_delta = None
        if self._first_public_delta_s is not None and self._api_started_at is not None:
            api_delta = max(
                0.0, self._started_at + self._first_public_delta_s - self._api_started_at
            )
        measurements = AttemptMeasurements(
            duration_s=duration_s,
            outcome=outcome,
            error_type=error_type,
            first_safe_progress_s=self._first_safe_progress_s,
            answer_ready_s=self._answer_ready_s,
            first_public_delta_s=self._first_public_delta_s,
            api_first_public_delta_s=api_delta,
            model_calls=self._model_calls,
            tool_calls=self._tool_calls,
            model_retries=max(0, self._model_calls - self._logical_model_calls),
            tool_retries=max(0, self._tool_calls - self._logical_tool_calls),
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            result_counts=dict(self._result_counts),
        )
        with self.activate():
            for cleanup in self._cleanups.values():
                self._safe_call(cleanup)
            self._cleanups.clear()
            self._set_root_summary(measurements)
            if error_type:
                self._safe_call(mark_failed, self._span, error_type)
            self._safe_call(self._instruments.record_attempt, measurements)
            self._emit_terminal_log(measurements, exception=exception)
        self._safe_call(self._span.end)

    def _set_root_summary(self, measurements: AttemptMeasurements) -> None:
        attributes: dict[str, AttributeValue] = {
            "app.outcome": measurements.outcome,
            "app.investigation.duration_s": measurements.duration_s,
            "app.investigation.model.calls": measurements.model_calls,
            "app.investigation.tool.calls": measurements.tool_calls,
            "app.investigation.model.retries": measurements.model_retries,
            "app.investigation.tool.retries": measurements.tool_retries,
            "app.investigation.result_count": sum(measurements.result_counts.values()),
        }
        for name, value in (
            ("app.investigation.first_safe_progress_s", measurements.first_safe_progress_s),
            ("app.investigation.answer_ready_s", measurements.answer_ready_s),
            ("app.investigation.first_public_delta_s", measurements.first_public_delta_s),
            ("gen_ai.usage.input_tokens", measurements.input_tokens),
            ("gen_ai.usage.output_tokens", measurements.output_tokens),
        ):
            if value is not None:
                attributes[name] = value
        for name, attribute_value in attributes.items():
            self._safe_call(self._span.set_attribute, name, attribute_value)

    def _emit_terminal_log(
        self,
        measurements: AttemptMeasurements,
        *,
        exception: BaseException | None,
    ) -> None:
        fields: dict[str, Any] = {
            "workflow_run_id": self.workflow_run_id,
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "attempt": self.attempt,
            "app.outcome": measurements.outcome,
            "duration_s": measurements.duration_s,
            "model_calls": measurements.model_calls,
            "tool_calls": measurements.tool_calls,
            "model_retries": measurements.model_retries,
            "tool_retries": measurements.tool_retries,
            "result_count": sum(measurements.result_counts.values()),
        }
        if measurements.error_type:
            fields[ERROR_TYPE] = measurements.error_type
        if exception is not None:
            fields["exception.type"] = type(exception).__name__
            fields["exception.stacktrace"] = _stacktrace_without_message(exception)
        if measurements.outcome == Outcome.SUCCESS:
            self._safe_call(self._logger.info, LogEvent.ATTEMPT_COMPLETED, **fields)
        elif not self._failure_logged:
            self._failure_logged = True
            event = (
                LogEvent.ATTEMPT_CANCELLED
                if measurements.outcome == Outcome.CANCELLED
                else LogEvent.ATTEMPT_FAILED
            )
            self._safe_call(self._logger.error, event, **fields)

    def _elapsed(self) -> float:
        return max(0.0, self._clock() - self._started_at)

    @staticmethod
    def _safe_call(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return function(*args, **kwargs)
        except Exception:
            return None


def _stacktrace_without_message(exc: BaseException) -> str:
    """Render actionable frames without copying provider or user content from the message."""
    frames = traceback.extract_tb(exc.__traceback__)
    rendered = ["Traceback (most recent call last):\n"]
    rendered.extend(
        f'  File "{frame.filename}", line {frame.lineno}, in {frame.name}\n' for frame in frames
    )
    rendered.append(f"{type(exc).__module__}.{type(exc).__qualname__}\n")
    return "".join(rendered)
