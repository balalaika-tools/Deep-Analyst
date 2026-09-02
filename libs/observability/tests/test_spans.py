import asyncio

import pytest
from observability import mark_failed, start_genai_span, start_span, workflow_run
from opentelemetry import context as otel_context
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Link, StatusCode


def test_escaping_exception_sets_status_and_error_type_without_events(
    span_exporter: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    provider, exporter = span_exporter
    tracer = provider.get_tracer("test")

    with pytest.raises(TimeoutError):
        with start_span("load cdr", tracer=tracer, attributes={"app.source": "cdr"}):
            raise TimeoutError("boom")

    (span,) = exporter.get_finished_spans()
    assert span.name == "load cdr"
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes is not None
    assert span.attributes["error.type"] == "TimeoutError"
    assert span.attributes["app.source"] == "cdr"
    assert span.events == ()


def test_success_leaves_status_unset_and_cancellation_is_a_real_class(
    span_exporter: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    provider, exporter = span_exporter
    tracer = provider.get_tracer("test")

    with start_span("ok", tracer=tracer):
        pass
    with pytest.raises(asyncio.CancelledError):
        with start_span("cancelled", tracer=tracer):
            raise asyncio.CancelledError

    ok, cancelled = exporter.get_finished_spans()
    assert ok.status.status_code is StatusCode.UNSET
    assert cancelled.attributes is not None
    assert cancelled.attributes["error.type"] == "CancelledError"


def test_mark_failed_marks_a_handled_failure(
    span_exporter: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    provider, exporter = span_exporter
    with start_span("handled", tracer=provider.get_tracer("test")) as span:
        mark_failed(span, "429")
    (span_data,) = exporter.get_finished_spans()
    assert span_data.status.status_code is StatusCode.ERROR
    assert span_data.attributes is not None
    assert span_data.attributes["error.type"] == "429"


def test_explicit_empty_context_starts_a_linked_root_trace(
    span_exporter: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    provider, exporter = span_exporter
    tracer = provider.get_tracer("test")

    with start_span("run ingestion", tracer=tracer) as coordinator:
        coordinator_context = coordinator.get_span_context()
        with start_genai_span(
            "ingest record",
            tracer=tracer,
            context=otel_context.Context(),
            links=[Link(coordinator_context)],
        ):
            pass

    by_name = {span.name: span for span in exporter.get_finished_spans()}
    coordinator_data = by_name["run ingestion"]
    record_data = by_name["ingest record"]
    assert record_data.parent is None
    assert record_data.context.trace_id != coordinator_data.context.trace_id
    assert [link.context for link in record_data.links] == [coordinator_data.context]


def test_genai_projection_is_ancestor_closed_and_shares_one_trace(
    span_exporter: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    provider, exporter = span_exporter
    tracer = provider.get_tracer("test")

    with start_genai_span("run ingestion", tracer=tracer):
        with workflow_run("run-42"):
            with start_span("load docs", tracer=tracer):
                pass
            with start_genai_span("index chunks", tracer=tracer):
                with start_genai_span("invoke_workflow indexing_embeddings", tracer=tracer):
                    with start_genai_span("embeddings titan", tracer=tracer):
                        pass
            with start_span("persist", tracer=tracer):
                pass

    spans = exporter.get_finished_spans()
    by_name = {span.name: span for span in spans}
    retained = {
        "run ingestion",
        "index chunks",
        "invoke_workflow indexing_embeddings",
        "embeddings titan",
    }
    trace_ids = {span.context.trace_id for span in spans}

    assert len(trace_ids) == 1
    for name in retained:
        attributes = by_name[name].attributes or {}
        assert attributes["app.telemetry.category"] == "genai"
        assert attributes["app.workflow.run.id"] == "run-42"
    for name in {"load docs", "persist"}:
        assert "app.telemetry.category" not in (by_name[name].attributes or {})
    for name in retained - {"run ingestion"}:
        parent = by_name[name].parent
        assert parent is not None
        assert any(
            candidate.context.span_id == parent.span_id and candidate.name in retained
            for candidate in spans
        )
