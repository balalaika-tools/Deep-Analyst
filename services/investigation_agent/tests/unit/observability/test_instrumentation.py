from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest
from investigation_agent.observability.events import InvestigationInstruments
from investigation_agent.observability.instrumentation import (
    AttemptTelemetry,
    InvestigationModelCallback,
    LogicalModelTelemetryMiddleware,
    LogicalToolTelemetryMiddleware,
    PhysicalToolTelemetryMiddleware,
)
from langchain.agents import create_agent
from langchain.agents.middleware import ToolRetryMiddleware
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult, LLMResult
from langchain_core.tools import BaseTool, tool
from observability.genai_metrics import GENAI_CONTENT_ATTRIBUTES, GenAIInstruments
from opentelemetry import trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode


@dataclass
class _Clock:
    value: float = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class _RecordingLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, Any], int]] = []

    def info(self, event: str, **fields: Any) -> None:
        self._record("info", event, fields)

    def error(self, event: str, **fields: Any) -> None:
        self._record("error", event, fields)

    def _record(self, level: str, event: str, fields: dict[str, Any]) -> None:
        context = trace.get_current_span().get_span_context()
        self.records.append((level, event, fields, context.trace_id))


@pytest.fixture
def telemetry() -> Iterator[
    tuple[
        TracerProvider,
        InMemorySpanExporter,
        MeterProvider,
        InMemoryMetricReader,
    ]
]:
    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[metric_reader])
    yield tracer_provider, span_exporter, meter_provider, metric_reader
    tracer_provider.shutdown()
    meter_provider.shutdown()


def _attempt(
    telemetry: tuple[TracerProvider, InMemorySpanExporter, MeterProvider, InMemoryMetricReader],
    *,
    run_id: str,
    clock: _Clock,
    logger: _RecordingLogger,
    prior: dict[str, str] | None = None,
) -> AttemptTelemetry:
    tracer_provider, _, meter_provider, _ = telemetry
    return AttemptTelemetry(
        tracer=tracer_provider.get_tracer("test.investigation"),
        instruments=InvestigationInstruments.create(meter_provider.get_meter("test.investigation")),
        workflow_run_id=run_id,
        thread_id="thread-sensitive-42",
        turn_id="turn-sensitive-9",
        attempt=1,
        prior_trace_carrier=prior,
        api_started_at=clock.value - 1,
        logger=logger,
        clock=clock,
    )


def _metric_points(reader: InMemoryMetricReader) -> list[tuple[str, Any]]:
    data = reader.get_metrics_data()
    assert data is not None
    return [
        (metric.name, point)
        for resource_metrics in data.resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
        for point in metric.data.data_points
    ]


class _ToolCallingModel(BaseChatModel):
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "tool-calling-test"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> _ToolCallingModel:
        del tools, tool_choice, kwargs
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del messages, stop, run_manager, kwargs
        self.calls += 1
        if self.calls == 1:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_evidence",
                        "args": {"query": "private query"},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            )
        else:
            message = AIMessage(content="finished")
        return ChatResult(generations=[ChatGeneration(message=message)])


def test_each_attempt_is_a_finite_root_and_resume_links_instead_of_parenting(
    telemetry: tuple[TracerProvider, InMemorySpanExporter, MeterProvider, InMemoryMetricReader],
) -> None:
    _, exporter, _, reader = telemetry
    clock = _Clock()
    logger = _RecordingLogger()
    first = _attempt(telemetry, run_id="run-1", clock=clock, logger=logger)
    with first.activate():
        with first.phase("input_guardrail"):
            clock.advance(0.2)
        first.record_first_safe_progress()
        first.record_results("evidence", 3)
        first.record_answer_ready()
        first.record_first_public_delta()
    carrier = first.trace_carrier()
    clock.advance(0.3)
    first.finish()

    resumed = _attempt(
        telemetry,
        run_id="run-2",
        clock=clock,
        logger=logger,
        prior={**carrier, "checkpoint_payload": "must-not-be-observed"},
    )
    clock.advance(0.1)
    resumed.finish()

    roots = [
        span for span in exporter.get_finished_spans() if span.name.endswith("investigation_turn")
    ]
    assert len(roots) == 2
    first_root, resumed_root = roots
    assert first_root.parent is None
    assert resumed_root.parent is None
    assert resumed_root.context.trace_id != first_root.context.trace_id
    assert len(resumed_root.links) == 1
    assert resumed_root.links[0].context.trace_id == first_root.context.trace_id
    assert "checkpoint_payload" not in str(resumed_root.attributes)

    assert [record[1] for record in logger.records] == [
        "investigation.attempt_completed",
        "investigation.attempt_completed",
    ]
    assert logger.records[0][3] == first_root.context.trace_id
    assert logger.records[1][3] == resumed_root.context.trace_id

    forbidden_metric_labels = {
        "workflow_run_id",
        "thread_id",
        "turn_id",
        "request_id",
        "trace_id",
        "span_id",
        "app.workflow.run.id",
        "app.investigation.thread.id",
        "app.investigation.turn.id",
    }
    for _, point in _metric_points(reader):
        assert forbidden_metric_labels.isdisjoint(point.attributes)
    metric_names = {name for name, _ in _metric_points(reader)}
    assert {
        "gen_ai.invoke_workflow.duration",
        "gen_ai.invoke_agent.duration",
        "gen_ai.invoke_agent.inference_calls",
        "gen_ai.invoke_agent.tool_calls",
        "app.investigation.api.time_to_first_chunk",
        "app.agent.time_to_first_chunk",
        "app.investigation.time_to_first_safe_progress",
        "app.investigation.answer_ready.duration",
        "app.investigation.completion.duration",
        "app.investigation.result_count",
    } <= metric_names


@pytest.mark.parametrize(
    ("outcome", "error_type"),
    [
        ("success", None),
        ("refused", "policy"),
        ("budget_exhausted", "budget"),
    ],
)
def test_all_non_exception_terminal_outcomes_close_the_attempt(
    telemetry: tuple[TracerProvider, InMemorySpanExporter, MeterProvider, InMemoryMetricReader],
    outcome: str,
    error_type: str | None,
) -> None:
    _, exporter, _, _ = telemetry
    attempt = _attempt(
        telemetry,
        run_id=f"run-{outcome}",
        clock=_Clock(),
        logger=_RecordingLogger(),
    )

    attempt.finish(outcome=outcome)
    attempt.finish(outcome=outcome)

    (root,) = exporter.get_finished_spans()
    assert root.attributes is not None
    assert root.attributes["app.outcome"] == outcome
    assert root.attributes.get("error.type") == error_type


@pytest.mark.asyncio
async def test_physical_retries_share_logical_ids_and_emit_one_aggregate(
    telemetry: tuple[TracerProvider, InMemorySpanExporter, MeterProvider, InMemoryMetricReader],
) -> None:
    tracer_provider, exporter, meter_provider, reader = telemetry
    clock = _Clock()
    logger = _RecordingLogger()
    attempt = _attempt(telemetry, run_id="run-retries", clock=clock, logger=logger)
    callback = InvestigationModelCallback(
        tracer=tracer_provider.get_tracer("test.model"),
        model_instruments=GenAIInstruments.create(meter_provider.get_meter("test.model")),
        investigation_instruments=attempt.instruments,
        capture_content=False,
        clock=clock,
    )

    with attempt.activate():
        with attempt.logical_operation("model"):
            failed_run = uuid4()
            await callback.on_chat_model_start(
                {},
                [[HumanMessage("private prompt")]],
                run_id=failed_run,
                metadata={"ls_model_name": "fake-model", "ls_provider": "amazon_bedrock"},
            )
            clock.advance(0.1)
            await callback.on_llm_error(TimeoutError("private provider detail"), run_id=failed_run)

            succeeded_run = uuid4()
            await callback.on_chat_model_start(
                {},
                [[HumanMessage("private prompt")]],
                run_id=succeeded_run,
                metadata={"ls_model_name": "fake-model", "ls_provider": "amazon_bedrock"},
            )
            clock.advance(0.05)
            await callback.on_llm_new_token("private token", run_id=succeeded_run)
            response = LLMResult(
                generations=[
                    [
                        ChatGeneration(
                            message=AIMessage(
                                content="private answer",
                                usage_metadata={
                                    "input_tokens": 7,
                                    "output_tokens": 2,
                                    "total_tokens": 9,
                                },
                            )
                        )
                    ]
                ]
            )
            await callback.on_llm_end(response, run_id=succeeded_run)

        with attempt.logical_operation("tool"):
            with pytest.raises(TimeoutError):
                with attempt.physical_tool_attempt("search_evidence"):
                    raise TimeoutError("private tool detail")
            with attempt.physical_tool_attempt("search_evidence"):
                pass
        attempt.record_results("evidence", 2)
    clock.advance(0.2)
    attempt.finish()

    spans = exporter.get_finished_spans()
    model_spans = [span for span in spans if span.name == "chat fake-model"]
    tool_spans = [span for span in spans if span.name == "execute_tool search_evidence"]
    assert len(model_spans) == len(tool_spans) == 2
    for physical_spans in (model_spans, tool_spans):
        assert all(span.attributes is not None for span in physical_spans)
        logical_ids = {
            (span.attributes or {})["app.gen_ai.logical_operation.id"] for span in physical_spans
        }
        assert len(logical_ids) == 1
        assert {
            (span.attributes or {})["app.gen_ai.physical_attempt"] for span in physical_spans
        } == {1, 2}
    assert model_spans[0].status.status_code is StatusCode.ERROR
    assert tool_spans[0].status.status_code is StatusCode.ERROR

    root = next(span for span in spans if span.name.endswith("investigation_turn"))
    assert root.attributes is not None
    assert root.attributes["app.investigation.model.calls"] == 2
    assert root.attributes["app.investigation.tool.calls"] == 2
    assert root.attributes["app.investigation.model.retries"] == 1
    assert root.attributes["app.investigation.tool.retries"] == 1
    assert root.attributes["gen_ai.usage.input_tokens"] == 7
    assert root.attributes["gen_ai.usage.output_tokens"] == 2

    points = _metric_points(reader)
    assert any(name == "gen_ai.client.operation.time_to_first_chunk" for name, _ in points)
    assert sum(name == "gen_ai.execute_tool.duration" for name, _ in points) == 2
    model_retry = next(point for name, point in points if name == "app.investigation.model.retries")
    tool_retry = next(point for name, point in points if name == "app.investigation.tool.retries")
    assert model_retry.sum == 1
    assert tool_retry.sum == 1


@pytest.mark.asyncio
async def test_langchain_middleware_order_traces_each_tool_retry_inside_one_operation(
    telemetry: tuple[TracerProvider, InMemorySpanExporter, MeterProvider, InMemoryMetricReader],
) -> None:
    tracer_provider, exporter, meter_provider, _ = telemetry
    clock = _Clock()
    logger = _RecordingLogger()
    attempt = _attempt(telemetry, run_id="run-agent", clock=clock, logger=logger)
    callback = InvestigationModelCallback(
        tracer=tracer_provider.get_tracer("test.agent-model"),
        model_instruments=GenAIInstruments.create(meter_provider.get_meter("test.agent-model")),
        investigation_instruments=attempt.instruments,
        capture_content=False,
        clock=clock,
    )
    tool_calls = 0

    @tool
    def search_evidence(query: str) -> str:
        """Search scoped evidence for the requested query."""
        nonlocal tool_calls
        del query
        tool_calls += 1
        if tool_calls == 1:
            raise TimeoutError("retryable private detail")
        return "private evidence result"

    model = _ToolCallingModel(callbacks=[callback])
    agent = create_agent(
        model,
        tools=[search_evidence],
        middleware=[
            LogicalModelTelemetryMiddleware(),
            LogicalToolTelemetryMiddleware(),
            ToolRetryMiddleware(
                max_retries=1,
                retry_on=(TimeoutError,),
                initial_delay=0,
                backoff_factor=1,
                jitter=False,
            ),
            PhysicalToolTelemetryMiddleware(known_tools=frozenset({"search_evidence"})),
        ],
    )

    with attempt.activate():
        await agent.ainvoke({"messages": [HumanMessage("private user input")]})
    attempt.finish()

    assert tool_calls == 2
    spans = exporter.get_finished_spans()
    tool_spans = [span for span in spans if span.name == "execute_tool search_evidence"]
    assert len(tool_spans) == 2
    assert {(span.attributes or {})["app.gen_ai.logical_operation.id"] for span in tool_spans} == {
        "run-agent:tool:1"
    }
    assert {(span.attributes or {})["app.gen_ai.physical_attempt"] for span in tool_spans} == {1, 2}
    model_spans = [span for span in spans if span.name.startswith("chat")]
    assert len(model_spans) == 2
    assert all("app.gen_ai.logical_operation.id" in (span.attributes or {}) for span in model_spans)
    root = next(span for span in spans if span.name.endswith("investigation_turn"))
    assert root.attributes is not None
    assert root.attributes["app.investigation.model.calls"] == 2
    assert root.attributes["app.investigation.tool.calls"] == 2
    assert root.attributes["app.investigation.tool.retries"] == 1


@pytest.mark.asyncio
async def test_capture_off_excludes_all_private_content_from_spans_and_logs(
    telemetry: tuple[TracerProvider, InMemorySpanExporter, MeterProvider, InMemoryMetricReader],
) -> None:
    tracer_provider, exporter, meter_provider, _ = telemetry
    clock = _Clock()
    logger = _RecordingLogger()
    attempt = _attempt(
        telemetry,
        run_id="run-private",
        clock=clock,
        logger=logger,
        prior={
            "traceparent": "invalid",
            "checkpoint": "private-checkpoint",
            "secret": "postgresql://user:private-secret@db/name",
        },
    )
    callback = InvestigationModelCallback(
        tracer=tracer_provider.get_tracer("test.model"),
        model_instruments=GenAIInstruments.create(meter_provider.get_meter("test.model")),
        investigation_instruments=attempt.instruments,
        capture_content=False,
        clock=clock,
    )
    canaries = {
        "private-user-text",
        "private-model-output",
        "private-evidence",
        "private-row",
        "SELECT private_sql",
        "private-tool-argument",
        "private-checkpoint",
        "postgresql://user:private-secret@db/name",
    }

    with attempt.activate():
        with attempt.logical_operation("model"):
            run_id = uuid4()
            await callback.on_chat_model_start(
                {},
                [[HumanMessage("private-user-text private-evidence private-row")]],
                run_id=run_id,
                metadata={"ls_model_name": "safe-model", "ls_provider": "amazon_bedrock"},
            )
            await callback.on_llm_end(
                LLMResult(
                    generations=[
                        [ChatGeneration(message=AIMessage(content="private-model-output"))]
                    ]
                ),
                run_id=run_id,
            )
        with attempt.logical_operation("tool"):
            with attempt.physical_tool_attempt("unknown_tool"):
                pass
    try:
        raise RuntimeError("private-tool-argument SELECT private_sql private-checkpoint")
    except RuntimeError as exc:
        attempt.fail(exc)

    spans = exporter.get_finished_spans()
    for span in spans:
        attributes = span.attributes or {}
        assert GENAI_CONTENT_ATTRIBUTES.isdisjoint(attributes)
        for canary in canaries:
            assert canary not in str(attributes)
    for record in logger.records:
        for canary in canaries:
            assert canary not in str(record)
    assert len(logger.records) == 1
    assert logger.records[0][0:2] == ("error", "investigation.attempt_failed")
    stacktrace = logger.records[0][2]["exception.stacktrace"]
    assert logger.records[0][2]["exception.type"] == "RuntimeError"
    assert "test_capture_off_excludes_all_private_content_from_spans_and_logs" in stacktrace
    assert "RuntimeError" in stacktrace


def test_handled_failure_is_retained_until_terminal_close(
    telemetry: tuple[TracerProvider, InMemorySpanExporter, MeterProvider, InMemoryMetricReader],
) -> None:
    clock = _Clock()
    logger = _RecordingLogger()
    attempt = _attempt(telemetry, run_id="run-handled", clock=clock, logger=logger)

    try:
        raise ValueError("provider detail must not enter telemetry")
    except ValueError as exc:
        attempt.record_handled_failure(exc)
    attempt.fail(None)

    assert len(logger.records) == 1
    fields = logger.records[0][2]
    assert fields["error.type"] == "internal"
    assert fields["exception.type"] == "ValueError"
    assert "provider detail" not in fields["exception.stacktrace"]


@pytest.mark.asyncio
async def test_cancellation_closes_root_once_and_context_is_not_current_across_yield(
    telemetry: tuple[TracerProvider, InMemorySpanExporter, MeterProvider, InMemoryMetricReader],
) -> None:
    tracer_provider, exporter, meter_provider, reader = telemetry
    clock = _Clock()
    logger = _RecordingLogger()
    attempt = _attempt(telemetry, run_id="run-cancelled", clock=clock, logger=logger)
    callback = InvestigationModelCallback(
        tracer=tracer_provider.get_tracer("test.cancelled-model"),
        model_instruments=GenAIInstruments.create(meter_provider.get_meter("test.cancelled-model")),
        investigation_instruments=attempt.instruments,
        capture_content=False,
        clock=clock,
    )
    with attempt.activate():
        with attempt.logical_operation("model"):
            await callback.on_chat_model_start(
                {},
                [[HumanMessage("private pending request")]],
                run_id=uuid4(),
                metadata={"ls_model_name": "safe-model", "ls_provider": "amazon_bedrock"},
            )
    assert callback.open_runs == 1

    async def source() -> AsyncIterator[str]:
        yield "progress"
        raise asyncio.CancelledError

    received: list[str] = []
    with pytest.raises(asyncio.CancelledError):
        async for item in attempt.trace_stream(source()):
            received.append(item)
            assert not trace.get_current_span().get_span_context().is_valid
    attempt.cancel()

    assert received == ["progress"]
    root = next(
        span for span in exporter.get_finished_spans() if span.name.endswith("investigation_turn")
    )
    assert root.status.status_code is StatusCode.ERROR
    assert root.attributes is not None
    assert root.attributes["error.type"] == "CancelledError"
    assert len(logger.records) == 1
    assert logger.records[0][1] == "agent_invocation_cancelled"
    assert callback.open_runs == 0
    abandoned = next(
        span for span in exporter.get_finished_spans() if span.name == "chat safe-model"
    )
    assert abandoned.status.status_code is StatusCode.ERROR
    assert abandoned.attributes is not None
    assert abandoned.attributes["error.type"] == "_ABANDONED"
    cancellation = next(
        point for name, point in _metric_points(reader) if name == "app.agent.cancellations"
    )
    assert cancellation.value == 1


class _RaisingExporter(SpanExporter):
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        del spans
        raise RuntimeError("export unavailable")


class _RaisingLogger:
    def info(self, event: str, **fields: Any) -> None:
        del event, fields
        raise RuntimeError("log exporter unavailable")

    def error(self, event: str, **fields: Any) -> None:
        del event, fields
        raise RuntimeError("log exporter unavailable")


class _RaisingInstruments:
    def record_attempt(self, measurements: Any) -> None:
        del measurements
        raise RuntimeError("metric exporter unavailable")


@pytest.mark.asyncio
async def test_exporter_failures_do_not_change_the_business_result() -> None:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(_RaisingExporter()))
    meter_provider = MeterProvider()
    attempt = AttemptTelemetry(
        tracer=provider.get_tracer("test.broken-export"),
        instruments=_RaisingInstruments(),  # type: ignore[arg-type]
        workflow_run_id="run-export-failure",
        thread_id="thread-1",
        turn_id="turn-1",
        attempt=1,
        logger=_RaisingLogger(),
    )
    callback = InvestigationModelCallback(
        tracer=provider.get_tracer("test.broken-model-export"),
        model_instruments=GenAIInstruments.create(meter_provider.get_meter("test.model")),
        investigation_instruments=attempt.instruments,
        capture_content=False,
    )
    result = {"status": "completed"}

    with attempt.activate():
        with attempt.logical_operation("model"):
            run_id = uuid4()
            await callback.on_chat_model_start(
                {},
                [[HumanMessage("business input")]],
                run_id=run_id,
                metadata={"ls_model_name": "safe-model", "ls_provider": "amazon_bedrock"},
            )
            await callback.on_llm_end(
                LLMResult(
                    generations=[[ChatGeneration(message=AIMessage(content="business result"))]]
                ),
                run_id=run_id,
            )
        with attempt.phase("commit_answer"):
            pass
    attempt.finish()

    assert result == {"status": "completed"}
    assert attempt.closed
    provider.shutdown()
    meter_provider.shutdown()
