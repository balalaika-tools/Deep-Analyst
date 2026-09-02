"""Span helpers that apply the error contract: status plus `error.type`, no span events."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import Link, Span, SpanKind, Status, StatusCode
from opentelemetry.util.types import AttributeValue

ERROR_TYPE = "error.type"
TELEMETRY_CATEGORY = "app.telemetry.category"
GENAI_CATEGORY = "genai"
WORKFLOW_RUN_ID = "app.workflow.run.id"

_workflow_run_id: ContextVar[str | None] = ContextVar("workflow_run_id", default=None)


def error_type_of(exc: BaseException) -> str:
    """A bounded value for `error.type`: a provider error code when the SDK exposes one
    (botocore's `response["Error"]["Code"]`), otherwise the exception class name."""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = (
            response.get("Error", {}).get("Code")
            if isinstance(response.get("Error"), dict)
            else None
        )
        if code:
            return str(code)
    return type(exc).__name__


def mark_failed(span: Span, error_type: str) -> None:
    """Mark a handled failure on a span that will not see the exception escape."""
    span.set_status(Status(StatusCode.ERROR))
    span.set_attribute(ERROR_TYPE, error_type)


def genai_span_attributes(
    attributes: Mapping[str, AttributeValue] | None = None,
) -> dict[str, AttributeValue]:
    """Return the centralized Langfuse-projection attributes for one retained span."""
    classified = dict(attributes or {})
    classified[TELEMETRY_CATEGORY] = GENAI_CATEGORY
    if run_id := _workflow_run_id.get():
        classified[WORKFLOW_RUN_ID] = run_id
    return classified


@contextmanager
def workflow_run(run_id: str) -> Iterator[None]:
    """Make a business run ID available to classified descendants.

    The current span is the bounded run root; setting it here lets a run ID created
    inside that root remain directly searchable without starting a replacement trace.
    """
    current = trace.get_current_span()
    if current.is_recording():
        current.set_attribute(WORKFLOW_RUN_ID, run_id)
    token = _workflow_run_id.set(run_id)
    try:
        yield
    finally:
        _workflow_run_id.reset(token)


@contextmanager
def start_span(
    name: str,
    *,
    tracer: trace.Tracer,
    attributes: Mapping[str, AttributeValue] | None = None,
    kind: SpanKind = SpanKind.INTERNAL,
    context: otel_context.Context | None = None,
    links: Sequence[Link] | None = None,
) -> Iterator[Span]:
    """Start a current span; an escaping exception sets ERROR status and `error.type`.

    The exception itself is re-raised for the boundary that owns the log record.
    Cancellation is a real class (`CancelledError`) and is recorded like any other.
    Pass an explicit empty context plus links when the operation owns a new linked trace.
    """
    with tracer.start_as_current_span(
        name,
        kind=kind,
        attributes=attributes,
        context=context,
        links=links,
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        try:
            yield span
        except BaseException as exc:
            mark_failed(span, error_type_of(exc))
            raise


@contextmanager
def start_genai_span(
    name: str,
    *,
    tracer: trace.Tracer,
    attributes: Mapping[str, AttributeValue] | None = None,
    kind: SpanKind = SpanKind.INTERNAL,
    context: otel_context.Context | None = None,
    links: Sequence[Link] | None = None,
) -> Iterator[Span]:
    """Start a span retained by the ancestor-closed Langfuse projection."""
    with start_span(
        name,
        tracer=tracer,
        attributes=genai_span_attributes(attributes),
        kind=kind,
        context=context,
        links=links,
    ) as span:
        yield span
