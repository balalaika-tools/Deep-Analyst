from typing import Any

import pytest
from ingestion.application.ingest_dataset import IngestionPlan, ingest_dataset
from ingestion.genai.entity_extraction.agent import build_entity_agent
from ingestion.genai.entity_extraction.extractor import AgentEntityExtractor
from ingestion.genai.relationship_extraction.agent import build_relationship_agent
from ingestion.genai.relationship_extraction.extractor import AgentRelationshipExtractor
from ingestion.genai.shared.throttle import ModelThrottle
from observability import start_genai_span
from observability.genai_metrics import GenAIInstruments
from observability.langchain import OTelModelCallback
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

Telemetry = tuple[TracerProvider, InMemorySpanExporter, MeterProvider, InMemoryMetricReader]


@pytest.mark.asyncio
async def test_each_chunk_workflow_contains_both_agents_and_physical_chat_spans(
    plan: IngestionPlan,
    deps_factory: Any,
    telemetry: Telemetry,
    scripted_model_factory: Any,
    structured_call_factory: Any,
    throttle: ModelThrottle,
) -> None:
    tracer_provider, exporter, meter_provider, _ = telemetry
    tracer = tracer_provider.get_tracer("test")
    callback = OTelModelCallback(
        tracer=tracer,
        instruments=GenAIInstruments.create(meter_provider.get_meter("test")),
    )
    entity_model = scripted_model_factory(
        script=[structured_call_factory("EntityExtraction", {"entities": []})],
        callbacks=[callback],
    )
    relationship_model = scripted_model_factory(
        script=[structured_call_factory("RelationshipExtraction", {"relationships": []})],
        callbacks=[callback],
    )
    deps = deps_factory(systems=("docs",))
    deps.entity_extractor = AgentEntityExtractor(
        build_entity_agent(entity_model, max_retries=1),
        throttle=throttle,
        tracer=tracer,
    )
    deps.relationship_extractor = AgentRelationshipExtractor(
        build_relationship_agent(relationship_model, max_retries=1),
        throttle=throttle,
        tracer=tracer,
    )

    with start_genai_span("run ingestion", tracer=tracer) as root:
        await ingest_dataset(plan, deps)

    spans = exporter.get_finished_spans()
    workflows = [span for span in spans if span.name == "invoke_workflow extract_chunk"]
    assert len(workflows) == 10
    record_roots = [span for span in spans if span.name == "ingest record"]
    assert len(record_roots) == 10
    assert all(record.parent is None for record in record_roots)
    assert all(
        [link.context.trace_id for link in record.links] == [root.get_span_context().trace_id]
        for record in record_roots
    )
    for workflow in workflows:
        attributes = workflow.attributes or {}
        assert attributes["gen_ai.operation.name"] == "invoke_workflow"
        assert attributes["gen_ai.workflow.name"] == "extract_chunk"
        assert attributes["app.workflow.run.id"] == "run-1"
        record_id = attributes["app.ingestion.record_id"]
        chunk_id = attributes["app.ingestion.chunk_id"]
        assert isinstance(record_id, str) and record_id.startswith("docs:")
        assert isinstance(chunk_id, str) and chunk_id.startswith(record_id)
        assert isinstance(attributes["app.ingestion.chunk_index"], int)
        record_root = next(
            record
            for record in record_roots
            if record.context.trace_id == workflow.context.trace_id
        )
        assert workflow.parent is not None
        assert workflow.parent.span_id == record_root.context.span_id
        agents = [
            span
            for span in spans
            if span.parent is not None and span.parent.span_id == workflow.context.span_id
        ]
        assert {agent.name for agent in agents} == {
            "invoke_agent entity_extraction",
            "invoke_agent relationship_extraction",
        }
        for agent in agents:
            chats = [
                span
                for span in spans
                if span.parent is not None and span.parent.span_id == agent.context.span_id
            ]
            assert len(chats) == 1 and chats[0].name == "chat fake-chat"
