"""LangChain model callbacks that contribute to attempt aggregates."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any
from uuid import UUID

from langchain_core.outputs import ChatGenerationChunk, GenerationChunk, LLMResult
from observability import mark_failed
from observability.genai_metrics import GenAIInstruments, TokenUsage
from observability.langchain import OTelModelCallback, resolve_provider, resolve_request_model
from opentelemetry.trace import Tracer

from investigation_agent.observability.events import InvestigationInstruments
from investigation_agent.observability.instrumentation.attempt import (
    AttemptTelemetry,
    current_attempt,
)
from investigation_agent.observability.instrumentation.context import ModelObservation


def _token_usage(response: LLMResult) -> TokenUsage | None:
    input_tokens = 0
    output_tokens = 0
    saw_input = False
    saw_output = False
    for generations in response.generations:
        for generation in generations:
            message = getattr(generation, "message", None)
            usage = TokenUsage.from_usage_metadata(getattr(message, "usage_metadata", None))
            if usage is None:
                continue
            if usage.input_tokens is not None:
                input_tokens += usage.input_tokens
                saw_input = True
            if usage.output_tokens is not None:
                output_tokens += usage.output_tokens
                saw_output = True
    if not saw_input and not saw_output:
        return None
    return TokenUsage(
        input_tokens=input_tokens if saw_input else None,
        output_tokens=output_tokens if saw_output else None,
    )


class InvestigationModelCallback(OTelModelCallback):
    """Add attempt aggregates to the shared one-span-per-physical-call callback."""

    def __init__(
        self,
        *,
        tracer: Tracer,
        model_instruments: GenAIInstruments,
        investigation_instruments: InvestigationInstruments,
        capture_content: bool = False,
        separate_system_instructions: bool = False,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        super().__init__(
            tracer=tracer,
            instruments=model_instruments,
            capture_content=capture_content,
            separate_system_instructions=separate_system_instructions,
        )
        self._investigation_instruments = investigation_instruments
        self._clock = clock
        self._observations: dict[UUID, ModelObservation] = {}

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        attempt = current_attempt()
        operation_id: str | None = None
        physical_attempt = 1
        if attempt is not None:
            operation_id, physical_attempt = attempt.record_model_attempt()
            attempt.register_cleanup(self, lambda: self._abandon_attempt(attempt))
        self._observations[run_id] = ModelObservation(
            attempt=attempt,
            started_at=self._clock(),
            provider=resolve_provider(metadata),
            model=resolve_request_model(serialized, metadata, kwargs),
            operation_id=operation_id,
            physical_attempt=physical_attempt,
        )
        try:
            await super().on_chat_model_start(
                serialized,
                messages,
                run_id=run_id,
                parent_run_id=parent_run_id,
                tags=tags,
                metadata=metadata,
                **kwargs,
            )
        except Exception:
            return
        run = self._runs.get(run_id)
        if run is not None and operation_id is not None:
            AttemptTelemetry._safe_call(
                run.span.set_attribute,
                "app.gen_ai.logical_operation.id",
                operation_id,
            )
            AttemptTelemetry._safe_call(
                run.span.set_attribute,
                "app.gen_ai.physical_attempt",
                physical_attempt,
            )

    async def on_llm_new_token(
        self,
        token: str | list[str | dict[str, Any]],
        *,
        chunk: GenerationChunk | ChatGenerationChunk | None = None,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        del token, chunk, parent_run_id, tags, kwargs
        observation = self._observations.get(run_id)
        if observation is None or observation.first_chunk_seen:
            return
        observation.first_chunk_seen = True
        duration_s = max(0.0, self._clock() - observation.started_at)
        run = self._runs.get(run_id)
        if run is not None:
            AttemptTelemetry._safe_call(
                run.span.set_attribute,
                "gen_ai.response.time_to_first_chunk",
                duration_s,
            )
        AttemptTelemetry._safe_call(
            self._investigation_instruments.record_model_time_to_first_chunk,
            duration_s,
            provider=observation.provider,
            request_model=observation.model,
        )

    async def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        observation = self._observations.pop(run_id, None)
        run = self._runs.get(run_id)
        if observation is not None and observation.attempt is not None:
            observation.attempt.record_token_usage(_token_usage(response))
        try:
            await super().on_llm_end(
                response,
                run_id=run_id,
                parent_run_id=parent_run_id,
                **kwargs,
            )
        except Exception:
            if run is not None and run.span.is_recording():
                AttemptTelemetry._safe_call(run.span.end)

    async def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        self._observations.pop(run_id, None)
        run = self._runs.get(run_id)
        try:
            await super().on_llm_error(
                error,
                run_id=run_id,
                parent_run_id=parent_run_id,
                **kwargs,
            )
        except Exception:
            if run is not None and run.span.is_recording():
                AttemptTelemetry._safe_call(run.span.end)

    def abandon_open_runs(self) -> None:
        self._observations.clear()
        spans = [run.span for run in self._runs.values()]
        try:
            super().abandon_open_runs()
        except Exception:
            for span in spans:
                if span.is_recording():
                    AttemptTelemetry._safe_call(span.end)

    def _abandon_attempt(self, attempt: AttemptTelemetry) -> None:
        for run_id, observation in list(self._observations.items()):
            if observation.attempt is not attempt:
                continue
            self._observations.pop(run_id, None)
            run = self._runs.pop(run_id, None)
            if run is None:
                continue
            AttemptTelemetry._safe_call(mark_failed, run.span, "_ABANDONED")
            AttemptTelemetry._safe_call(
                self._instruments.record_operation,
                duration_s=max(0.0, time.perf_counter() - run.started_at),
                operation="chat",
                provider=run.provider,
                request_model=run.model,
                error_type="_ABANDONED",
            )
            AttemptTelemetry._safe_call(run.span.end)
