"""Hook phases and physical attempts appear under the attempt root without any content."""

from __future__ import annotations

import json
from typing import Any

import pytest
from investigation_agent.domain.history import TurnStatus
from investigation_agent.observability.events import ATTEMPT_SPAN_NAME, InvestigationInstruments
from investigation_agent.observability.instrumentation import (
    AttemptTelemetryFactory,
    LogicalModelTelemetryMiddleware,
    LogicalToolTelemetryMiddleware,
    PhysicalToolTelemetryMiddleware,
)
from langchain_core.messages import AIMessage
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

PII_TEXT = "Alexandra Mavridou, phone +30 210 555 0101, account GR123"


@pytest.mark.asyncio
async def test_hook_phases_and_tool_attempts_nest_under_one_root_without_content(
    support: Any,
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader]).get_meter("test")
    factory = AttemptTelemetryFactory(
        tracer=provider.get_tracer("test"), instruments=InvestigationInstruments.create(meter)
    )

    answer = f"{PII_TEXT} received 50 [chunk-1]."
    responses = [
        AIMessage(
            content="",
            tool_calls=[
                support.tool_call(
                    "search_evidence", {"intent": {"question": PII_TEXT, "objective": "o"}}, "c1"
                )
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                support.draft_call(
                    "c2", answer=answer, claims=[support.claim("k1", answer, "chunk-1")]
                )
            ],
        ),
    ]
    behaviour = support.FakeToolBehaviour(
        outcomes={
            "search_evidence": [
                support.outcome(
                    "search_evidence", items=[support.evidence("chunk-1", content=PII_TEXT)]
                )
            ]
        }
    )
    harness = support.build_harness(
        responses, behaviour=behaviour, verifier_results=[support.entailed("k1")]
    )
    # Rebuild with the telemetry middleware stack the runtime installs.
    from investigation_agent.genai.investigation.agent import (
        AgentComponents,
        build_investigation_agent,
    )

    components = AgentComponents(
        model=harness.model,
        tools=support.fake_tools(behaviour),
        guardrail=support.allow_all(),
        evidence_guard=None,
        verifier=harness.verifier,
        closure=harness.closure,
        projection_model=harness.projection,
        retry_policy=support.POLICY,
        transient_errors=(TimeoutError,),
        telemetry=(
            LogicalModelTelemetryMiddleware(),
            LogicalToolTelemetryMiddleware(),
            PhysicalToolTelemetryMiddleware(
                known_tools=frozenset({"search_evidence", "query_records", "find_connections"})
            ),
        ),
    )
    harness.agent = build_investigation_agent(
        components, limits=support.limits(), checkpointer=harness.saver
    )
    attempt = factory.create(
        thread_id="thread-1", turn_id="turn-x", attempt=1, prior_trace_carrier=None
    )

    with attempt.activate():
        state, _ = await harness.run_turn(message=PII_TEXT)
    attempt.finish()

    assert state.turn is not None and state.turn.status is TurnStatus.COMPLETED
    assert (
        PII_TEXT in state.history.messages[0].content
        and "GR123" in state.history.messages[-1].content
    )
    spans = exporter.get_finished_spans()
    by_name = {span.name for span in spans}
    assert ATTEMPT_SPAN_NAME in by_name
    for expected in (
        "input_guardrail",
        "verify_grounding",
        "turn_close",
        "execute_tool search_evidence",
        "model_operation",
        "tool_operation",
    ):
        assert expected in by_name, expected
    roots = [s for s in spans if s.parent is None]
    assert [s.name for s in roots] == [ATTEMPT_SPAN_NAME]
    root_id = roots[0].context.trace_id
    assert all(s.context.trace_id == root_id for s in spans)
    serialized = json.dumps(
        [[s.name, {k: str(v) for k, v in (s.attributes or {}).items()}] for s in spans]
    )
    for private in ("Mavridou", "GR123", "555 0101", "chunk-1", "SELECT", "received 50"):
        assert private not in serialized
    assert sum(1 for s in spans if s.name.startswith("execute_tool")) == 1
