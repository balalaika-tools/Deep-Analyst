"""One finite root per attempt, resume links, aggregate-only streaming, and content-off signals."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from investigation_agent.api.sse import stream_prepared_turn
from investigation_agent.application.invoke_turn import InvocationPolicy, InvokeRequest, InvokeTurn
from investigation_agent.application.thread_locks import ThreadLockRegistry
from investigation_agent.core.context import RuntimeContext
from investigation_agent.domain.history import (
    TurnStatus,
    append_assistant_message,
    append_user_message,
    stable_assistant_message_id,
)
from investigation_agent.domain.investigation_state import InvestigationState, parse_state
from investigation_agent.observability.events import ATTEMPT_SPAN_NAME, InvestigationInstruments
from investigation_agent.observability.instrumentation import AttemptTelemetryFactory
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

NOW = datetime(2026, 3, 4, 5, 6, tzinfo=UTC)
PII_MESSAGE = "Call +30 210 555 0101 about account GR123 held by Alexandra Mavridou"
PII_ANSWER = "Account GR123 belongs to Alexandra Mavridou; phone +30 210 555 0101 [chunk-1]. " * 8
type TelemetryFixture = tuple[AttemptTelemetryFactory, InMemorySpanExporter, InMemoryMetricReader]


@dataclass(frozen=True)
class Snapshot:
    values: Mapping[str, Any]


@dataclass
class Graph:
    values: dict[str, Any] | None = None
    interrupt_first: bool = False
    runs: int = 0

    async def aget_state(self, config: Mapping[str, object]) -> Snapshot:
        del config
        return Snapshot(self.values or {})

    async def astream(
        self,
        input: Mapping[str, Any] | None,
        config: Mapping[str, object],
        *,
        context: RuntimeContext,
        stream_mode: list[str],
        durability: str,
        version: str,
    ) -> AsyncIterator[object]:
        del config, context, stream_mode, durability, version
        self.runs += 1
        if input is not None:
            self.values = dict(input)
        yield {
            "type": "custom",
            "ns": (),
            "data": {"phase": "searching_evidence", "tool": "search_evidence", "attempt": 1},
        }
        if self.interrupt_first and self.runs == 1:
            raise RuntimeError("process stopped mid-turn")
        assert self.values is not None
        self.values = _committed(self.values)


def _committed(values: Mapping[str, Any]) -> dict[str, Any]:
    state = parse_state(values)
    assert state is not None and state.turn is not None
    turn = state.turn
    history = append_user_message(
        state.history,
        message_id=turn.user_message_id,
        turn_id=turn.turn_id,
        request_id=turn.request_id,
        content=turn.utterance,
        created_at=turn.opened_at,
        max_turns=10,
    )
    assistant_id = stable_assistant_message_id(turn.turn_id)
    history = append_assistant_message(
        history,
        message_id=assistant_id,
        turn_id=turn.turn_id,
        request_id=turn.request_id,
        content=PII_ANSWER,
        created_at=NOW,
    )
    return InvestigationState(
        control=state.control,
        turn=turn.model_copy(
            update={
                "status": TurnStatus.COMPLETED,
                "assistant_message_id": assistant_id,
                "intake_complete": True,
            }
        ),
        history=history,
    ).as_update()


@pytest.fixture
def telemetry() -> Iterator[TelemetryFixture]:
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[reader])
    factory = AttemptTelemetryFactory(
        tracer=tracer_provider.get_tracer("test"),
        instruments=InvestigationInstruments.create(meter_provider.get_meter("test")),
    )
    yield factory, exporter, reader
    tracer_provider.shutdown()


def _service(graph: Graph, factory: AttemptTelemetryFactory) -> InvokeTurn:
    return InvokeTurn(
        graph=graph,
        locks=ThreadLockRegistry(),
        policy=InvocationPolicy(
            policy_version="p", max_message_chars=4_000, turn_timeout_s=30, max_history_turns=10
        ),
        clock=lambda: NOW,
        telemetry=factory,
    )


async def _stream(service: InvokeTurn) -> list[dict[str, Any]]:
    prepared = await service.prepare(
        InvokeRequest(
            request_id="request-1", thread_id="thread-1", case_id="case-1", message=PII_MESSAGE
        )
    )
    return [
        json.loads(e["data"])
        async for e in stream_prepared_turn(prepared, chunk_chars=40, clock=lambda: NOW)
    ]


@pytest.mark.asyncio
async def test_completed_turn_has_one_finite_root_and_no_per_delta_signals(
    telemetry: TelemetryFixture,
) -> None:
    factory, exporter, reader = telemetry
    events = await _stream(_service(Graph(), factory))

    deltas = [e for e in events if e["event"] == "answer.delta"]
    assert len(deltas) > 3 and events[-1]["event"] == "run.completed"
    spans = exporter.get_finished_spans()
    roots = [s for s in spans if s.name == ATTEMPT_SPAN_NAME]
    assert (
        len(roots) == 1
        and roots[0].parent is None
        and roots[0].status.status_code is StatusCode.UNSET
    )
    assert len(spans) == 1
    root = roots[0]
    attributes = root.attributes
    assert attributes is not None
    assert attributes["app.investigation.thread.id"] == "thread-1"
    first_delta = attributes["app.investigation.first_public_delta_s"]
    answer_ready = attributes["app.investigation.answer_ready_s"]
    assert isinstance(first_delta, (int, float)) and first_delta >= 0
    assert isinstance(answer_ready, (int, float)) and answer_ready >= 0
    assert attributes["app.outcome"] == "success"
    serialized = json.dumps({k: str(v) for k, v in attributes.items()})
    for private in ("GR123", "Mavridou", "555 0101", "chunk-1"):
        assert private not in serialized
    metrics = reader.get_metrics_data()
    assert metrics is not None
    names = {
        m.name for rm in metrics.resource_metrics for sm in rm.scope_metrics for m in sm.metrics
    }
    assert "app.investigation.completion.duration" in names
    labels = {
        k
        for rm in metrics.resource_metrics
        for sm in rm.scope_metrics
        for m in sm.metrics
        for dp in m.data.data_points
        for k in (dp.attributes or {})
    }
    assert not {k for k in labels if "thread" in k or "turn" in k or "run_id" in k}


@pytest.mark.asyncio
async def test_resumed_turn_starts_a_new_root_linked_to_the_prior_attempt(
    telemetry: TelemetryFixture,
) -> None:
    factory, exporter, _ = telemetry
    graph = Graph(interrupt_first=True)
    service = _service(graph, factory)

    first = await _stream(service)
    assert first[-1]["event"] == "run.failed" and first[-1]["data"]["code"] == "internal"
    assert graph.values is not None and graph.values["turn"]["prior_trace_carrier"]
    second = await _stream(service)
    assert second[-1]["event"] == "run.completed" and graph.runs == 2

    roots = [s for s in exporter.get_finished_spans() if s.name == ATTEMPT_SPAN_NAME]
    assert len(roots) == 2
    failed, resumed = roots
    assert failed.attributes is not None and resumed.attributes is not None
    assert (
        failed.status.status_code is StatusCode.ERROR
        and failed.attributes["app.investigation.attempt"] == 1
    )
    assert resumed.parent is None and resumed.attributes["app.investigation.attempt"] == 2
    assert resumed.context.trace_id != failed.context.trace_id
    assert [link.context.trace_id for link in resumed.links] == [failed.context.trace_id]
    assert (
        resumed.attributes["app.investigation.turn.id"]
        == failed.attributes["app.investigation.turn.id"]
    )


@pytest.mark.asyncio
async def test_replay_and_disconnect_do_not_leave_open_roots(
    telemetry: TelemetryFixture,
) -> None:
    factory, exporter, _ = telemetry
    graph = Graph()
    service = _service(graph, factory)
    await _stream(service)
    prepared = await service.prepare(
        InvokeRequest(
            request_id="request-1", thread_id="thread-1", case_id="case-1", message=PII_MESSAGE
        )
    )
    assert prepared.telemetry is None
    events = [
        json.loads(e["data"])
        async for e in stream_prepared_turn(prepared, chunk_chars=40, clock=lambda: NOW)
    ]
    assert events[-1]["event"] == "run.completed" and graph.runs == 1

    disconnected_graph = Graph()
    disconnected = await _service(disconnected_graph, factory).prepare(
        InvokeRequest(
            request_id="request-9", thread_id="thread-9", case_id="case-1", message="hello"
        )
    )
    probes = 0

    async def probe() -> bool:
        nonlocal probes
        probes += 1
        return probes > 1

    out = [
        e
        async for e in stream_prepared_turn(
            disconnected, chunk_chars=40, disconnected=probe, clock=lambda: NOW
        )
    ]
    assert len(out) == 1
    roots = [s for s in exporter.get_finished_spans() if s.name == ATTEMPT_SPAN_NAME]
    assert len(roots) == 2
    attributes = roots[-1].attributes
    assert attributes is not None and attributes["app.outcome"] == "cancelled"
