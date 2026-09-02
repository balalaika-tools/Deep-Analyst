"""Fakes for the GenAI boundary: a scripted tool-calling chat model and an embeddings handle."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from ingestion.genai.shared.throttle import ModelThrottle
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


class ScriptedToolModel(BaseChatModel):
    """Returns scripted messages or raises scripted exceptions, one per physical call."""

    model_name: str = "fake-chat"
    script: list[Any] = []
    calls: int = 0
    received: list[list[BaseMessage]] = []

    def bind_tools(
        self, tools: Any, *, tool_choice: str | None = None, **kwargs: Any
    ) -> Runnable[LanguageModelInput, AIMessage]:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.received.append(list(messages))
        self.calls += 1
        item = self.script[min(self.calls - 1, len(self.script) - 1)]
        if isinstance(item, BaseException):
            raise item
        return ChatResult(generations=[ChatGeneration(message=item)])

    @property
    def _llm_type(self) -> str:
        return "scripted"


class ScriptedNativeModel(ScriptedToolModel):
    """Native-JSON model profile that rejects any tool-selection request."""

    bind_requests: list[dict[str, Any]] = []

    def bind_tools(self, tools: Any, **kwargs: Any) -> Runnable[LanguageModelInput, AIMessage]:
        if "tool_choice" in kwargs:
            raise AssertionError("native structured output must not request tool_choice")
        self.bind_requests.append({"tools": list(tools), **kwargs})
        return self


def structured_call(tool_name: str, payload: dict[str, Any]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": tool_name, "args": payload, "id": "call-1", "type": "tool_call"}],
        usage_metadata={"input_tokens": 50, "output_tokens": 20, "total_tokens": 70},
    )


def native_response(payload: dict[str, Any]) -> AIMessage:
    return AIMessage(
        content=json.dumps(payload),
        usage_metadata={"input_tokens": 50, "output_tokens": 20, "total_tokens": 70},
    )


class FakeEmbeddings(Embeddings):
    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions
        self.batches: list[list[str]] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.batches.append(list(texts))
        return [[float(len(text))] * self.dimensions for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


@pytest.fixture
def scripted_model_factory() -> type[ScriptedToolModel]:
    return ScriptedToolModel


@pytest.fixture
def native_model_factory() -> type[ScriptedNativeModel]:
    return ScriptedNativeModel


@pytest.fixture
def structured_call_factory() -> Any:
    return structured_call


@pytest.fixture
def native_response_factory() -> Any:
    return native_response


@pytest.fixture
def fake_embeddings_factory() -> type[FakeEmbeddings]:
    return FakeEmbeddings


@pytest.fixture
def telemetry() -> Iterator[
    tuple[TracerProvider, InMemorySpanExporter, MeterProvider, InMemoryMetricReader]
]:
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider(resource=Resource.create({}))
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(resource=Resource.create({}), metric_readers=[reader])
    yield tracer_provider, exporter, meter_provider, reader
    tracer_provider.shutdown()
    meter_provider.shutdown()


@pytest.fixture
def throttle() -> ModelThrottle:
    return ModelThrottle.create(requests_per_minute=6000, max_in_flight=4)
