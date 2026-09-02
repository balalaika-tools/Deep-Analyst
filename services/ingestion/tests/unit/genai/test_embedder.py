import asyncio

import pytest
from ingestion.genai.embeddings.embedder import BedrockTextEmbedder
from ingestion.genai.shared.throttle import ModelThrottle
from ingestion.ports.text_embedder import (
    EmbeddingInput,
    PermanentEmbeddingError,
    TransientEmbeddingError,
)
from langchain_core.embeddings import Embeddings
from observability import start_genai_span, workflow_run
from observability.genai_metrics import GenAIInstruments
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

Telemetry = tuple[TracerProvider, InMemorySpanExporter, MeterProvider, InMemoryMetricReader]


def _exception_leaves(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for nested in exc.exceptions for leaf in _exception_leaves(nested)]
    return [exc]


def _input(record_id: str, chunk_index: int, text: str) -> EmbeddingInput:
    start = chunk_index * 10
    return EmbeddingInput(
        source_system=record_id.split(":", 1)[0],
        record_id=record_id,
        chunk_id=f"{record_id}#{start}-{start + len(text)}",
        chunk_index=chunk_index,
        char_start=start,
        char_end=start + len(text),
        text=text,
    )


class ControlledEmbeddings(Embeddings):
    def __init__(self, values: dict[str, float], dimensions: int = 2) -> None:
        self.values = values
        self.dimensions = dimensions
        self.started = asyncio.Semaphore(0)
        self.finished = asyncio.Semaphore(0)
        self.release = {text: asyncio.Event() for text in values}
        self.started_texts: list[str] = []
        self.finished_texts: list[str] = []
        self.active = 0
        self.peak = 0

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("the Titan path must not call aembed_documents")

    def embed_query(self, text: str) -> list[float]:
        raise AssertionError("tests exercise the asynchronous physical request boundary")

    async def aembed_query(self, text: str) -> list[float]:
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.started_texts.append(text)
        self.started.release()
        await self.release[text].wait()
        self.finished_texts.append(text)
        self.finished.release()
        self.active -= 1
        return [self.values[text]] * self.dimensions


class RecordingThrottle(ModelThrottle):
    waits: int

    async def wait_for_request(self) -> None:
        self.waits = getattr(self, "waits", 0) + 1
        await super().wait_for_request()


def _embedder(
    telemetry: Telemetry,
    handle: Embeddings,
    throttle: ModelThrottle,
    *,
    dimensions: int = 2,
) -> BedrockTextEmbedder:
    tracer_provider, _, meter_provider, _ = telemetry
    return BedrockTextEmbedder(
        embeddings=handle,
        model_id="amazon.titan-embed-text-v2:0",
        dimensions=dimensions,
        throttle=throttle,
        tracer=tracer_provider.get_tracer("test"),
        instruments=GenAIInstruments.create(meter_provider.get_meter("test")),
    )


async def _wait_for_starts(handle: ControlledEmbeddings, count: int) -> None:
    for _ in range(count):
        await asyncio.wait_for(handle.started.acquire(), timeout=1)


@pytest.mark.asyncio
async def test_record_workflows_preserve_global_order_when_calls_finish_in_reverse(
    telemetry: Telemetry,
) -> None:
    tracer_provider, exporter, _, _ = telemetry
    inputs = [
        _input("docs:A", 0, "a0"),
        _input("docs:A", 1, "a1"),
        _input("email:B", 0, "b0"),
    ]
    handle = ControlledEmbeddings({"a0": 10.0, "a1": 11.0, "b0": 20.0})
    embedder = _embedder(
        telemetry,
        handle,
        ModelThrottle.create(requests_per_minute=6000, max_in_flight=10),
    )

    with start_genai_span("run ingestion", tracer=tracer_provider.get_tracer("test")):
        with workflow_run("run-42"):
            result_task = asyncio.create_task(embedder.embed(inputs))
            await _wait_for_starts(handle, len(inputs))
            for text in ("b0", "a1", "a0"):
                handle.release[text].set()
                await asyncio.wait_for(handle.finished.acquire(), timeout=1)
            vectors = await result_task

    assert vectors == [[10.0, 10.0], [11.0, 11.0], [20.0, 20.0]]
    assert handle.finished_texts == ["b0", "a1", "a0"]
    spans = exporter.get_finished_spans()
    workflows = [span for span in spans if span.name == "invoke_workflow indexing_embeddings"]
    assert len(workflows) == 2
    for workflow in workflows:
        attributes = workflow.attributes or {}
        assert attributes["gen_ai.operation.name"] == "invoke_workflow"
        assert attributes["gen_ai.workflow.name"] == "indexing_embeddings"
        assert attributes["app.workflow.run.id"] == "run-42"
        record_id = str(attributes["app.ingestion.record_id"])
        expected = [item for item in inputs if item.record_id == record_id]
        assert attributes["app.embedding.input_count"] == len(expected)
        children = [
            span
            for span in spans
            if span.parent is not None and span.parent.span_id == workflow.context.span_id
        ]
        assert len(children) == len(expected)
        assert {(span.attributes or {})["app.ingestion.chunk_id"] for span in children} == {
            item.chunk_id for item in expected
        }
        assert all(span.name == "embeddings amazon.titan-embed-text-v2:0" for span in children)
        assert all(item.text not in str(span.attributes) for span in children for item in inputs)
    trace_ids = {span.context.trace_id for span in spans}
    assert len(trace_ids) == 1


@pytest.mark.asyncio
async def test_each_physical_embedding_waits_and_respects_the_shared_in_flight_limit(
    telemetry: Telemetry,
) -> None:
    inputs = [_input("docs:A", index, f"chunk-{index}") for index in range(5)]
    handle = ControlledEmbeddings({item.text: float(index) for index, item in enumerate(inputs)})
    throttle = RecordingThrottle.create(requests_per_minute=6000, max_in_flight=2)
    embedder = _embedder(telemetry, handle, throttle)

    task = asyncio.create_task(embedder.embed(inputs))
    await _wait_for_starts(handle, 2)
    assert handle.active == 2
    for event in handle.release.values():
        event.set()
    vectors = await asyncio.wait_for(task, timeout=1)

    assert len(vectors) == len(inputs)
    assert handle.peak == 2
    assert throttle.waits == len(inputs)


@pytest.mark.asyncio
async def test_wrong_dimension_is_a_permanent_failure(telemetry: Telemetry) -> None:
    handle = ControlledEmbeddings({"x": 1.0}, dimensions=3)
    handle.release["x"].set()
    embedder = _embedder(
        telemetry,
        handle,
        ModelThrottle.create(requests_per_minute=6000, max_in_flight=1),
        dimensions=4,
    )

    with pytest.raises(ExceptionGroup) as caught:
        await embedder.embed([_input("docs:A", 0, "x")])
    (permanent,) = [
        exc for exc in _exception_leaves(caught.value) if isinstance(exc, PermanentEmbeddingError)
    ]
    assert "3 dimensions, configured 4" in str(permanent)


@pytest.mark.asyncio
async def test_provider_failures_are_translated_and_the_physical_span_is_marked(
    telemetry: Telemetry,
) -> None:
    class FailingEmbeddings(ControlledEmbeddings):
        async def aembed_query(self, text: str) -> list[float]:
            raise TimeoutError("read timed out")

    _, exporter, _, _ = telemetry
    embedder = _embedder(
        telemetry,
        FailingEmbeddings({"x": 1.0}),
        ModelThrottle.create(requests_per_minute=6000, max_in_flight=1),
    )

    with pytest.raises(ExceptionGroup) as caught:
        await embedder.embed([_input("docs:A", 0, "x")])
    assert any(isinstance(exc, TransientEmbeddingError) for exc in _exception_leaves(caught.value))

    model_spans = [
        span for span in exporter.get_finished_spans() if span.name.startswith("embeddings")
    ]
    (span,) = model_spans
    assert span.attributes is not None and span.attributes["error.type"] == "TimeoutError"
