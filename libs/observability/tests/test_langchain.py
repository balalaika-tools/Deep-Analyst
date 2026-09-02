import json
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult, LLMResult
from observability.genai_content import (
    serialize_llm_result,
    serialize_observation_input,
    serialize_observation_output,
)
from observability.genai_metrics import GENAI_CONTENT_ATTRIBUTES, GenAIInstruments
from observability.langchain import OTelModelCallback
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode


class FlakyChatModel(BaseChatModel):
    """Fails the first `failures` calls, then answers with usage metadata."""

    model_name: str = "fake-model"
    failures: int = 0
    calls: int = 0

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls += 1
        if self.calls <= self.failures:
            raise TimeoutError("throttled")
        message = AIMessage(
            content="the answer",
            usage_metadata={
                "input_tokens": 10,
                "output_tokens": 2,
                "total_tokens": 12,
                "input_token_details": {"cache_read": 4},
            },
            response_metadata={"model_name": "fake-model-v1", "stop_reason": "end_turn"},
        )
        return ChatResult(generations=[ChatGeneration(message=message)])

    @property
    def _llm_type(self) -> str:
        return "flaky"


def _callback(
    span_exporter: tuple[TracerProvider, InMemorySpanExporter],
    metric_reader: tuple[MeterProvider, InMemoryMetricReader],
    *,
    capture_content: bool,
    separate_system_instructions: bool = False,
) -> OTelModelCallback:
    tracer_provider, _ = span_exporter
    meter_provider, _ = metric_reader
    return OTelModelCallback(
        tracer=tracer_provider.get_tracer("test"),
        instruments=GenAIInstruments.create(meter_provider.get_meter("test")),
        capture_content=capture_content,
        separate_system_instructions=separate_system_instructions,
    )


def _points(reader: InMemoryMetricReader, name: str) -> list[Any]:
    data = reader.get_metrics_data()
    assert data is not None
    return [
        point
        for resource_metrics in data.resource_metrics
        for scope in resource_metrics.scope_metrics
        for metric in scope.metrics
        if metric.name == name
        for point in metric.data.data_points
    ]


@pytest.mark.asyncio
async def test_one_span_and_one_duration_observation_per_physical_attempt(
    span_exporter: tuple[TracerProvider, InMemorySpanExporter],
    metric_reader: tuple[MeterProvider, InMemoryMetricReader],
) -> None:
    _, exporter = span_exporter
    _, reader = metric_reader
    callback = _callback(span_exporter, metric_reader, capture_content=False)
    model = FlakyChatModel(failures=1).with_config(callbacks=[callback])

    with pytest.raises(TimeoutError):
        await model.ainvoke("hi")
    await model.ainvoke("hi")

    failed, succeeded = exporter.get_finished_spans()
    assert failed.name == succeeded.name == "chat fake-model"
    assert failed.status.status_code is StatusCode.ERROR
    assert failed.attributes is not None and failed.attributes["error.type"] == "TimeoutError"
    assert failed.events == ()
    assert succeeded.status.status_code is StatusCode.UNSET
    assert succeeded.attributes is not None
    assert succeeded.attributes["gen_ai.operation.name"] == "chat"
    assert succeeded.attributes["gen_ai.provider.name"] not in {"", "unknown"}
    assert succeeded.attributes["gen_ai.response.model"] == "fake-model-v1"
    assert succeeded.attributes["gen_ai.usage.input_tokens"] == 10
    assert succeeded.attributes["gen_ai.usage.output_tokens"] == 2
    assert succeeded.attributes["gen_ai.usage.cache_read.input_tokens"] == 4
    assert succeeded.attributes["gen_ai.response.finish_reasons"] == ("end_turn",)
    assert callback.open_runs == 0

    durations = _points(reader, "gen_ai.client.operation.duration")
    assert sorted(point.attributes.get("error.type", "") for point in durations) == [
        "",
        "TimeoutError",
    ]
    tokens = _points(reader, "gen_ai.client.token.usage")
    assert {(p.attributes["gen_ai.token.type"], p.sum) for p in tokens} == {
        ("input", 10),
        ("output", 2),
    }


@pytest.mark.asyncio
async def test_content_is_absent_when_capture_is_off_and_present_when_on(
    span_exporter: tuple[TracerProvider, InMemorySpanExporter],
    metric_reader: tuple[MeterProvider, InMemoryMetricReader],
) -> None:
    _, exporter = span_exporter
    off = FlakyChatModel().with_config(
        callbacks=[_callback(span_exporter, metric_reader, capture_content=False)]
    )
    on = FlakyChatModel().with_config(
        callbacks=[_callback(span_exporter, metric_reader, capture_content=True)]
    )

    await off.ainvoke("canary prompt")
    await on.ainvoke("canary prompt")

    quiet, captured = exporter.get_finished_spans()
    assert quiet.attributes is not None and captured.attributes is not None
    assert GENAI_CONTENT_ATTRIBUTES.isdisjoint(quiet.attributes)
    assert "canary prompt" not in str(quiet.attributes)
    assert '"canary prompt"' in str(captured.attributes["gen_ai.input.messages"])
    assert '"the answer"' in str(captured.attributes["gen_ai.output.messages"])
    observation_input = captured.attributes["app.gen_ai.observation.input"]
    observation_output = captured.attributes["app.gen_ai.observation.output"]
    assert isinstance(observation_input, str)
    assert isinstance(observation_output, str)
    assert json.loads(observation_input) == [{"role": "user", "content": "canary prompt"}]
    assert json.loads(observation_output) == "the answer"


@pytest.mark.asyncio
async def test_bedrock_structured_output_preserves_wire_shape_and_finish_reason(
    span_exporter: tuple[TracerProvider, InMemorySpanExporter],
    metric_reader: tuple[MeterProvider, InMemoryMetricReader],
) -> None:
    _, exporter = span_exporter
    callback = _callback(
        span_exporter,
        metric_reader,
        capture_content=True,
        separate_system_instructions=True,
    )
    run_id = uuid4()
    await callback.on_chat_model_start(
        {},
        [[SystemMessage("extract entities"), HumanMessage("ok, see you there")]],
        run_id=run_id,
        metadata={"ls_model_name": "nova", "ls_provider": "amazon_bedrock"},
        invocation_params={"response_format": {"type": "json_schema"}},
    )
    message = AIMessage(
        content='{"entities": []}',
        response_metadata={"stopReason": "end_turn", "model_name": "nova-v1"},
        usage_metadata={"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
    )
    await callback.on_llm_end(
        LLMResult(generations=[[ChatGeneration(message=message)]]),
        run_id=run_id,
    )

    (span,) = exporter.get_finished_spans()
    assert span.attributes is not None
    system_instructions = span.attributes["gen_ai.system_instructions"]
    input_messages = span.attributes["gen_ai.input.messages"]
    output_messages = span.attributes["gen_ai.output.messages"]
    assert isinstance(system_instructions, str)
    assert isinstance(input_messages, str)
    assert isinstance(output_messages, str)
    assert json.loads(system_instructions) == [{"type": "text", "content": "extract entities"}]
    assert json.loads(input_messages) == [
        {
            "role": "user",
            "parts": [{"type": "text", "content": "ok, see you there"}],
        }
    ]
    assert json.loads(output_messages) == [
        {
            "role": "assistant",
            "parts": [{"type": "text", "content": '{"entities": []}'}],
            "finish_reason": "end_turn",
        }
    ]
    assert span.attributes["gen_ai.output.type"] == "json"
    assert span.attributes["gen_ai.response.finish_reasons"] == ("end_turn",)
    observation_input = span.attributes["app.gen_ai.observation.input"]
    observation_output = span.attributes["app.gen_ai.observation.output"]
    assert isinstance(observation_input, str)
    assert isinstance(observation_output, str)
    assert json.loads(observation_input) == [
        {"role": "system", "content": "extract entities"},
        {"role": "user", "content": "ok, see you there"},
    ]
    assert json.loads(observation_output) == {"entities": []}


def test_observation_serializers_optimize_only_lossless_text_shapes() -> None:
    messages = [[SystemMessage("extract entities"), HumanMessage("source text")]]
    structured_response = LLMResult(
        generations=[
            [
                ChatGeneration(
                    message=AIMessage(
                        content='{"entities": []}',
                        response_metadata={"finish_reason": "stop"},
                    )
                )
            ]
        ]
    )

    assert json.loads(serialize_observation_input(messages)) == [
        {"role": "system", "content": "extract entities"},
        {"role": "user", "content": "source text"},
    ]
    assert json.loads(serialize_observation_output(structured_response, output_type="json")) == {
        "entities": []
    }

    multipart_response = LLMResult(
        generations=[
            [
                ChatGeneration(
                    message=AIMessage(
                        content=[
                            {"type": "json", "json": {"entities": []}},
                            {"type": "text", "text": "done"},
                        ],
                        response_metadata={"finish_reason": "stop"},
                    )
                )
            ]
        ]
    )
    assert json.loads(serialize_observation_output(multipart_response, output_type="json")) == (
        json.loads(serialize_llm_result(multipart_response))
    )


def test_observation_output_omits_only_empty_reasoning_from_presentation() -> None:
    response = LLMResult(
        generations=[
            [
                ChatGeneration(
                    message=AIMessage(
                        content=[
                            {"type": "reasoning", "content": ""},
                            {"type": "text", "text": '{"entities": [{"text": "A. Mavridis"}]}'},
                        ],
                        response_metadata={"finish_reason": "end_turn"},
                    )
                )
            ]
        ]
    )

    canonical = json.loads(serialize_llm_result(response))
    assert canonical[0]["parts"][0] == {"type": "reasoning", "content": ""}
    assert json.loads(serialize_observation_output(response, output_type="json")) == {
        "entities": [{"text": "A. Mavridis"}]
    }


def test_observation_output_preserves_nonempty_reasoning_in_canonical_fallback() -> None:
    response = LLMResult(
        generations=[
            [
                ChatGeneration(
                    message=AIMessage(
                        content=[
                            {"type": "reasoning", "content": "material reasoning"},
                            {"type": "text", "text": '{"entities": []}'},
                        ],
                        response_metadata={"finish_reason": "end_turn"},
                    )
                )
            ]
        ]
    )

    assert json.loads(serialize_observation_output(response, output_type="json")) == json.loads(
        serialize_llm_result(response)
    )


def test_provider_content_blocks_are_not_silently_flattened_or_dropped() -> None:
    message = AIMessage(
        content=[
            {"type": "json", "json": {"entities": []}},
            {"type": "text", "text": "done"},
        ],
        response_metadata={"finishReason": "stop"},
    )
    response = LLMResult(generations=[[ChatGeneration(message=message)]])

    assert json.loads(serialize_llm_result(response)) == [
        {
            "role": "assistant",
            "parts": [
                {"type": "json", "json": {"entities": []}},
                {"type": "text", "content": "done"},
            ],
            "finish_reason": "stop",
        }
    ]


@pytest.mark.asyncio
async def test_model_spans_nest_under_the_current_span(
    span_exporter: tuple[TracerProvider, InMemorySpanExporter],
    metric_reader: tuple[MeterProvider, InMemoryMetricReader],
) -> None:
    tracer_provider, exporter = span_exporter
    model = FlakyChatModel().with_config(
        callbacks=[_callback(span_exporter, metric_reader, capture_content=False)]
    )

    with tracer_provider.get_tracer("t").start_as_current_span("invoke_agent x") as parent:
        await model.ainvoke("hi")

    chat, agent = exporter.get_finished_spans()
    assert chat.parent is not None and chat.parent.span_id == parent.get_span_context().span_id


@pytest.mark.asyncio
async def test_abandoned_runs_are_closed_with_the_sentinel(
    span_exporter: tuple[TracerProvider, InMemorySpanExporter],
    metric_reader: tuple[MeterProvider, InMemoryMetricReader],
) -> None:
    _, exporter = span_exporter
    _, reader = metric_reader
    callback = _callback(span_exporter, metric_reader, capture_content=False)

    # A run that never reports an outcome: no on_llm_end and no on_llm_error.
    await callback.on_chat_model_start(
        {}, [[HumanMessage("hi")]], run_id=uuid4(), metadata={"ls_model_name": "m"}
    )
    assert callback.open_runs == 1
    callback.abandon_open_runs()

    (span,) = exporter.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes is not None and span.attributes["error.type"] == "_ABANDONED"
    assert callback.open_runs == 0
    (duration,) = _points(reader, "gen_ai.client.operation.duration")
    assert duration.attributes["error.type"] == "_ABANDONED"
