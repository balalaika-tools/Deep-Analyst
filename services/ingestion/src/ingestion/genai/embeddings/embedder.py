"""Bedrock text embeddings grouped by record, with one span per physical request."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence

from langchain_core.embeddings import Embeddings
from observability import error_type_of, start_genai_span
from observability.genai_metrics import GenAIInstruments
from opentelemetry.trace import SpanKind, Tracer
from opentelemetry.util.types import AttributeValue

from ingestion.genai.shared.failures import translate_provider_error
from ingestion.genai.shared.throttle import ModelThrottle
from ingestion.ports.text_embedder import EmbeddingInput, PermanentEmbeddingError


class BedrockTextEmbedder:
    def __init__(
        self,
        *,
        embeddings: Embeddings,
        model_id: str,
        dimensions: int,
        throttle: ModelThrottle,
        tracer: Tracer,
        instruments: GenAIInstruments,
    ) -> None:
        self._embeddings = embeddings
        self._model_id = model_id
        self._dimensions = dimensions
        self._throttle = throttle
        self._tracer = tracer
        self._instruments = instruments

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, inputs: Sequence[EmbeddingInput]) -> list[list[float]]:
        """Embed record groups concurrently and return vectors in original global order."""
        if not inputs:
            return []
        groups: dict[tuple[str, str], list[tuple[int, EmbeddingInput]]] = {}
        for position, item in enumerate(inputs):
            groups.setdefault((item.source_system, item.record_id), []).append((position, item))

        async with asyncio.TaskGroup() as task_group:
            tasks = [task_group.create_task(self._embed_record(group)) for group in groups.values()]

        ordered: list[list[float] | None] = [None] * len(inputs)
        for task in tasks:
            for position, vector in task.result():
                ordered[position] = vector
        if any(vector is None for vector in ordered):
            raise RuntimeError("embedding result ordering invariant was violated")
        return [vector for vector in ordered if vector is not None]

    async def _embed_record(
        self, group: list[tuple[int, EmbeddingInput]]
    ) -> list[tuple[int, list[float]]]:
        first = group[0][1]
        attributes: dict[str, AttributeValue] = {
            "gen_ai.operation.name": "invoke_workflow",
            "gen_ai.workflow.name": "indexing_embeddings",
            "app.ingestion.record_id": first.record_id,
            "app.ingestion.source_system": first.source_system,
            "app.embedding.input_count": len(group),
        }
        with start_genai_span(
            "invoke_workflow indexing_embeddings",
            tracer=self._tracer,
            attributes=attributes,
        ):
            async with asyncio.TaskGroup() as task_group:
                tasks = [task_group.create_task(self._embed_one(item)) for _, item in group]
        return [(position, task.result()) for (position, _), task in zip(group, tasks, strict=True)]

    async def _embed_one(self, item: EmbeddingInput) -> list[float]:
        await self._throttle.wait_for_request()
        async with self._throttle.slot():
            started_at = time.perf_counter()
            try:
                with start_genai_span(
                    f"embeddings {self._model_id}",
                    tracer=self._tracer,
                    kind=SpanKind.CLIENT,
                    attributes=self._model_attributes(item),
                ):
                    vector = await self._embeddings.aembed_query(item.text)
                    if len(vector) != self._dimensions:
                        raise PermanentEmbeddingError(
                            f"embedding has {len(vector)} dimensions, configured {self._dimensions}"
                        )
            except Exception as exc:
                self._record_duration(started_at, error_type=error_type_of(exc))
                raise translate_provider_error(exc, operation="embeddings") from exc
            self._record_duration(started_at)
            return list(vector)

    def _model_attributes(self, item: EmbeddingInput) -> dict[str, AttributeValue]:
        return {
            "gen_ai.operation.name": "embeddings",
            "gen_ai.provider.name": "aws.bedrock",
            "gen_ai.request.model": self._model_id,
            "app.ingestion.record_id": item.record_id,
            "app.ingestion.source_system": item.source_system,
            "app.ingestion.chunk_id": item.chunk_id,
            "app.ingestion.chunk_index": item.chunk_index,
            "app.ingestion.chunk_char_start": item.char_start,
            "app.ingestion.chunk_char_end": item.char_end,
        }

    def _record_duration(self, started_at: float, *, error_type: str | None = None) -> None:
        self._instruments.record_operation(
            duration_s=time.perf_counter() - started_at,
            operation="embeddings",
            provider="aws.bedrock",
            request_model=self._model_id,
            error_type=error_type,
        )
