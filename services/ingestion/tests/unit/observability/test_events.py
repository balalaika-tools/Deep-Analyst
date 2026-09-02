from collections import Counter
from typing import Any

import pytest
from ingestion.application.ingest_case import IngestionPlan, ingest_case
from ingestion.observability.events import IngestionInstruments
from observability import start_genai_span
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, NumberDataPoint
from opentelemetry.trace import StatusCode


def _points(reader: InMemoryMetricReader, name: str) -> dict[tuple[tuple[str, Any], ...], int]:
    data = reader.get_metrics_data()
    assert data is not None
    points: dict[tuple[tuple[str, Any], ...], int] = {}
    for resource_metrics in data.resource_metrics:
        for scope in resource_metrics.scope_metrics:
            for metric in scope.metrics:
                if metric.name != name:
                    continue
                for point in metric.data.data_points:
                    assert isinstance(point, NumberDataPoint)
                    points[tuple(sorted((point.attributes or {}).items()))] = int(point.value)
    return points


def _all_metric_attribute_names(reader: InMemoryMetricReader) -> set[str]:
    data = reader.get_metrics_data()
    assert data is not None
    return {
        key
        for resource_metrics in data.resource_metrics
        for scope in resource_metrics.scope_metrics
        for metric in scope.metrics
        for point in metric.data.data_points
        for key in (point.attributes or {})
    }


def test_candidate_and_chunk_counters_carry_only_bounded_labels() -> None:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    instruments = IngestionInstruments.create(provider.get_meter("t"))

    instruments.record_candidates("entity", Counter({"accepted": 3, "rejected_span": 1}))
    instruments.record_chunks("docs", 10)
    instruments.record_chunks("cdr", 0)

    candidates = _points(reader, "app.ingestion.candidates")
    assert candidates == {
        (("kind", "entity"), ("outcome", "accepted")): 3,
        (("kind", "entity"), ("outcome", "rejected_span")): 1,
    }
    assert _points(reader, "app.ingestion.chunks_indexed") == {(("source_system", "docs"),): 10}


@pytest.mark.asyncio
async def test_a_fake_run_emits_the_root_span_source_spans_and_candidate_counters(
    plan: IngestionPlan, deps_factory: Any, telemetry: Any
) -> None:
    tracer_provider, exporter, _, reader = telemetry
    deps = deps_factory(systems=("cdr", "extraction", "email", "docs"))

    with start_genai_span("run ingestion", tracer=tracer_provider.get_tracer("t")) as root:
        await ingest_case(plan, deps)

    spans = exporter.get_finished_spans()
    by_name = Counter(span.name for span in spans)
    assert (
        by_name["load cdr"]
        == by_name["load extraction"]
        == by_name["load email"]
        == by_name["load docs"]
        == 1
    )
    assert by_name["ingest record"] == 33
    assert by_name["invoke_workflow extract_chunk"] == 33
    assert by_name["finalize ingestion"] == 1
    children = [
        s
        for s in spans
        if s.parent is not None and s.parent.span_id == root.get_span_context().span_id
    ]
    assert {s.name for s in children} >= {
        "load cdr",
        "load docs",
    }
    linked_roots = [span for span in spans if span.name in {"ingest record", "finalize ingestion"}]
    assert all(span.parent is None for span in linked_roots)
    assert all(
        [link.context.trace_id for link in span.links] == [root.get_span_context().trace_id]
        for span in linked_roots
    )
    assert all(span.context.trace_id != root.get_span_context().trace_id for span in linked_roots)
    record_trace_ids = {
        span.context.trace_id for span in linked_roots if span.name == "ingest record"
    }
    assert len(record_trace_ids) == 33
    finalization = next(span for span in linked_roots if span.name == "finalize ingestion")
    assert (finalization.attributes or {})["app.workflow.run.id"] == "run-1"
    assert (finalization.attributes or {})["app.outcome"] == "success"
    counters = _points(reader, "app.ingestion.candidates")
    assert counters[(("kind", "entity"), ("outcome", "accepted"))] == 1
    assert counters[(("kind", "relationship"), ("outcome", "accepted"))] == 1
    assert _points(reader, "app.ingestion.chunks_indexed") == {
        (("source_system", "docs"),): 10,
        (("source_system", "email"),): 6,
        (("source_system", "extraction"),): 17,
    }
    classified = [
        span for span in spans if (span.attributes or {}).get("app.telemetry.category") == "genai"
    ]
    assert classified
    assert all((span.attributes or {})["app.workflow.run.id"] == "run-1" for span in classified)
    root_span = next(
        span for span in spans if span.context.span_id == root.get_span_context().span_id
    )
    assert (root_span.attributes or {})["app.workflow.run.id"] == "run-1"
    assert _all_metric_attribute_names(reader).isdisjoint(
        {
            "app.workflow.run.id",
            "workflow_run_id",
            "trace_id",
            "span_id",
            "app.ingestion.record_id",
            "app.ingestion.chunk_id",
        }
    )


@pytest.mark.asyncio
async def test_failed_record_attempt_is_an_errored_root_linked_to_the_coordinator(
    plan: IngestionPlan, deps_factory: Any, telemetry: Any
) -> None:
    tracer_provider, exporter, _, _ = telemetry
    extractor_type = type(deps_factory().entity_extractor)
    deps = deps_factory(systems=("docs",), extractors=extractor_type(fail_on="R-03"))

    with start_genai_span("run ingestion", tracer=tracer_provider.get_tracer("t")) as root:
        with pytest.raises(ExceptionGroup):
            await ingest_case(plan, deps)

    failed = next(
        span
        for span in exporter.get_finished_spans()
        if span.name == "ingest record"
        and (span.attributes or {}).get("app.ingestion.record_id") == "docs:R-03"
    )
    assert failed.parent is None
    assert [link.context.trace_id for link in failed.links] == [root.get_span_context().trace_id]
    assert failed.status.status_code is StatusCode.ERROR
    assert (failed.attributes or {})["app.outcome"] == "error"
