"""LangChain model callback: one `chat <model>` span per physical model request.

Requires the `langchain` extra. The callback fires below every middleware, so each
retry attempt produces its own span, duration observation, and token usage.
Prompt and completion content is recorded only when `capture_content` is true.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult
from opentelemetry.trace import Span, SpanKind, Status, StatusCode, Tracer

from observability.genai_content import (
    resolve_finish_reason,
    serialize_chat_model_input,
    serialize_llm_result,
    serialize_observation_input,
    serialize_observation_output,
)
from observability.genai_metrics import (
    APP_OBSERVATION_INPUT,
    APP_OBSERVATION_OUTPUT,
    GENAI_FINISH_REASONS,
    GENAI_INPUT_MESSAGES,
    GENAI_OPERATION_NAME,
    GENAI_OUTPUT_MESSAGES,
    GENAI_OUTPUT_TYPE,
    GENAI_PROVIDER_NAME,
    GENAI_REQUEST_MODEL,
    GENAI_RESPONSE_ID,
    GENAI_RESPONSE_MODEL,
    GENAI_SYSTEM_INSTRUCTIONS,
    GenAIInstruments,
    TokenUsage,
    set_usage_attributes,
)
from observability.spans import ERROR_TYPE, error_type_of, genai_span_attributes

_PROVIDER_NAMES = {
    "amazon_bedrock": "aws.bedrock",
    "bedrock": "aws.bedrock",
    "bedrock_converse": "aws.bedrock",
    "openai": "openai",
    "anthropic": "anthropic",
    "azure": "azure.ai.openai",
    "azure_openai": "azure.ai.openai",
    "google_genai": "gcp.gemini",
    "google_vertexai": "gcp.vertex_ai",
}


def resolve_request_model(
    serialized: dict[str, Any] | None, metadata: dict[str, Any] | None, kwargs: dict[str, Any]
) -> str | None:
    """Best-effort model identity; omitted rather than guessed when unknown."""
    candidates: list[Any] = [(metadata or {}).get("ls_model_name")]
    invocation = kwargs.get("invocation_params") or {}
    candidates += [invocation.get(key) for key in ("model", "model_name", "model_id")]
    serialized_kwargs = (serialized or {}).get("kwargs") or {}
    candidates += [serialized_kwargs.get(key) for key in ("model", "model_name", "model_id")]
    for value in candidates:
        if isinstance(value, str) and value:
            return value
    return None


def resolve_provider(metadata: dict[str, Any] | None) -> str:
    provider = (metadata or {}).get("ls_provider")
    if not isinstance(provider, str) or not provider:
        return "unknown"
    return _PROVIDER_NAMES.get(provider, provider)


def resolve_output_type(kwargs: dict[str, Any]) -> str | None:
    """Resolve a requested JSON response from current LangChain/provider shapes."""
    invocation = kwargs.get("invocation_params") or {}
    if not isinstance(invocation, dict):
        return None
    response_format = invocation.get("response_format") or {}
    if not isinstance(response_format, dict):
        response_format = {}
    if response_format.get("type") in {"json_object", "json_schema"}:
        return "json"

    output_config = invocation.get("output_config") or invocation.get("outputConfig") or {}
    if not isinstance(output_config, dict):
        output_config = {}
    text_format = output_config.get("textFormat") or output_config.get("text_format") or {}
    if isinstance(text_format, dict) and str(text_format.get("type", "")).startswith("json"):
        return "json"

    options = kwargs.get("options") or {}
    if not isinstance(options, dict):
        return None
    structured = options.get("ls_structured_output_format") or {}
    if not isinstance(structured, dict):
        return None
    structured_kwargs = structured.get("kwargs") or {}
    method = structured_kwargs.get("method") if isinstance(structured_kwargs, dict) else None
    return "json" if method in {"json_mode", "json_schema"} else None


@dataclass(slots=True)
class _ResponseFacts:
    model: str | None = None
    response_id: str | None = None
    finish_reasons: list[str] = field(default_factory=list)
    usage: TokenUsage | None = None


def _response_facts(response: LLMResult) -> _ResponseFacts:
    facts = _ResponseFacts()
    for generations in response.generations:
        for generation in generations:
            message = getattr(generation, "message", None)
            metadata = getattr(message, "response_metadata", None) or {}
            facts.model = facts.model or metadata.get("model_name") or metadata.get("model")
            facts.response_id = facts.response_id or getattr(message, "id", None)
            reason = resolve_finish_reason(metadata, generation.generation_info)
            if reason:
                facts.finish_reasons.append(str(reason))
            if facts.usage is None:
                facts.usage = TokenUsage.from_usage_metadata(
                    getattr(message, "usage_metadata", None)
                )
    return facts


@dataclass(slots=True)
class _Run:
    span: Span
    started_at: float
    model: str | None
    provider: str
    output_type: str | None


class OTelModelCallback(AsyncCallbackHandler):
    """Async LangChain callback producing GenAI client spans and metrics."""

    def __init__(
        self,
        *,
        tracer: Tracer,
        instruments: GenAIInstruments,
        capture_content: bool = False,
        separate_system_instructions: bool = False,
    ) -> None:
        self._tracer = tracer
        self._instruments = instruments
        self._capture_content = capture_content
        self._separate_system_instructions = separate_system_instructions
        # Keyed by run_id: LangChain runs model calls concurrently.
        self._runs: dict[UUID, _Run] = {}

    @property
    def open_runs(self) -> int:
        return len(self._runs)

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        model = resolve_request_model(serialized, metadata, kwargs)
        provider = resolve_provider(metadata)
        attributes: dict[str, Any] = {GENAI_OPERATION_NAME: "chat", GENAI_PROVIDER_NAME: provider}
        if model:
            attributes[GENAI_REQUEST_MODEL] = model
        output_type = resolve_output_type(kwargs)
        if output_type:
            attributes[GENAI_OUTPUT_TYPE] = output_type
        invocation = kwargs.get("invocation_params") or {}
        for attribute, key in (
            ("gen_ai.request.temperature", "temperature"),
            ("gen_ai.request.max_tokens", "max_tokens"),
            ("gen_ai.request.top_p", "top_p"),
        ):
            if invocation.get(key) is not None:
                attributes[attribute] = invocation[key]
        # start_span, not start_as_current_span: the callback returns before the model
        # call finishes. The parent is whatever span is current right now.
        span = self._tracer.start_span(
            f"chat {model}" if model else "chat",
            kind=SpanKind.CLIENT,
            attributes=genai_span_attributes(attributes),
        )
        if self._capture_content and messages:
            system, captured_input, batch_size = serialize_chat_model_input(
                messages,
                separate_system_instructions=self._separate_system_instructions,
            )
            if system is not None:
                span.set_attribute(GENAI_SYSTEM_INSTRUCTIONS, system)
            span.set_attribute(GENAI_INPUT_MESSAGES, captured_input)
            span.set_attribute(APP_OBSERVATION_INPUT, serialize_observation_input(messages))
            span.set_attribute("app.gen_ai.input.batch_size", batch_size)
            if batch_size > 1:
                span.set_attribute("app.gen_ai.input.capture_mode", "truncated")
        self._runs[run_id] = _Run(
            span=span,
            started_at=time.perf_counter(),
            model=model,
            provider=provider,
            output_type=output_type,
        )

    async def on_llm_end(
        self, response: LLMResult, *, run_id: UUID, parent_run_id: UUID | None = None, **kwargs: Any
    ) -> None:
        run = self._runs.pop(run_id, None)
        if run is None:
            return
        facts = _response_facts(response)
        if facts.model:
            run.span.set_attribute(GENAI_RESPONSE_MODEL, facts.model)
        if facts.response_id:
            run.span.set_attribute(GENAI_RESPONSE_ID, facts.response_id)
        if facts.finish_reasons:
            run.span.set_attribute(GENAI_FINISH_REASONS, facts.finish_reasons)
        set_usage_attributes(run.span, facts.usage)
        if self._capture_content:
            run.span.set_attribute(GENAI_OUTPUT_MESSAGES, serialize_llm_result(response))
            run.span.set_attribute(
                APP_OBSERVATION_OUTPUT,
                serialize_observation_output(response, output_type=run.output_type),
            )
        self._instruments.record_operation(
            duration_s=time.perf_counter() - run.started_at,
            operation="chat",
            provider=run.provider,
            request_model=run.model,
            response_model=facts.model,
            usage=facts.usage,
        )
        run.span.end()

    async def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        run = self._runs.pop(run_id, None)
        if run is None:
            return
        error_type = error_type_of(error)
        # No record_exception: the boundary that handles the failure logs it once.
        run.span.set_status(Status(StatusCode.ERROR))
        run.span.set_attribute(ERROR_TYPE, error_type)
        self._instruments.record_operation(
            duration_s=time.perf_counter() - run.started_at,
            operation="chat",
            provider=run.provider,
            request_model=run.model,
            error_type=error_type,
        )
        run.span.end()

    def abandon_open_runs(self) -> None:
        """End spans whose run never reported an outcome, for example after cancellation."""
        for run_id in list(self._runs):
            run = self._runs.pop(run_id)
            run.span.set_status(Status(StatusCode.ERROR))
            run.span.set_attribute(ERROR_TYPE, "_ABANDONED")
            self._instruments.record_operation(
                duration_s=time.perf_counter() - run.started_at,
                operation="chat",
                provider=run.provider,
                request_model=run.model,
                error_type="_ABANDONED",
            )
            run.span.end()
