"""Fake ports for the ingestion use case: in-memory store, ledger, receipts, and models."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from evidence_model import EntityDraft, RelationshipDraft
from ingestion.adapters.fixtures import cdr, documents, email, extraction
from ingestion.application.ingest_case import IngestionDependencies, IngestionPlan
from ingestion.domain.candidates import EntityCandidate, RelationshipCandidate
from ingestion.domain.records import SourceBatch
from ingestion.observability.events import IngestionInstruments
from ingestion.ports.entity_extractor import ExtractionInput
from ingestion.ports.evidence_store import ChunkItem
from ingestion.ports.ingestion_ledger import ChunkingConfig, Receipt, RunStart
from ingestion.ports.relationship_extractor import KnownEntity
from ingestion.ports.text_embedder import EmbeddingInput
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

CASE = "case_trg_001"


class FakeSources:
    def __init__(self, edition_dir: Path, systems: Sequence[str]) -> None:
        self._edition_dir = edition_dir
        self._systems = systems
        self.loaded: list[str] = []

    @property
    def source_systems(self) -> Sequence[str]:
        return self._systems

    async def load(self, source_system: str) -> SourceBatch:
        self.loaded.append(source_system)
        loaders = {
            "cdr": cdr.load_cdr,
            "extraction": extraction.load_extraction,
            "email": email.load_emails,
            "docs": documents.load_documents,
        }
        return loaders[source_system](self._edition_dir, CASE)


class FakeStore:
    """Records what was persisted, in the order it was persisted."""

    def __init__(self) -> None:
        self.sources: list[str] = []
        self.chunks: list[ChunkItem] = []
        self.entities: list[EntityDraft] = []
        self.relationships: list[RelationshipDraft] = []
        self.runs: dict[str, dict[str, Any]] = {}
        self.completed: set[tuple[str, str]] = set()

    async def persist_source(self, batch: SourceBatch) -> None:
        self.sources.append(batch.source_system)

    async def persist_chunks(self, items: Sequence[ChunkItem]) -> int:
        self.chunks = list(items)
        return len(items)

    async def persist_graph(
        self, entities: Iterable[EntityDraft], relationships: Iterable[RelationshipDraft]
    ) -> tuple[int, int]:
        self.entities = sorted(entities, key=lambda e: e.entity_id)
        self.relationships = sorted(relationships, key=lambda r: r.relationship_id)
        return len(self.entities), len(self.relationships)

    async def has_completed(self, case_id: str, fingerprint: str) -> bool:
        return (case_id, fingerprint) in self.completed

    async def start(self, run: RunStart) -> str:
        run_id = f"run-{len(self.runs) + 1}"
        self.runs[run_id] = {"outcome": "running", "fingerprint": run.fingerprint}
        return run_id

    async def complete(
        self, run_id: str, *, completed_at: datetime, summary: dict[str, Any]
    ) -> None:
        self.runs[run_id].update(outcome="completed", summary=summary)
        self.completed.add((CASE, self.runs[run_id]["fingerprint"]))

    async def fail(self, run_id: str, *, completed_at: datetime, error_type: str) -> None:
        self.runs[run_id].update(outcome="failed", error_type=error_type)


class MemoryReceipts:
    def __init__(self) -> None:
        self.receipts: dict[str, Receipt] = {}

    def read(self, edition: str) -> Receipt | None:
        return self.receipts.get(edition)

    def write(self, edition: str, receipt: Receipt) -> None:
        self.receipts[edition] = receipt


class FakeEmbedder:
    model_id = "fake-embed"
    dimensions = 4

    def __init__(self) -> None:
        self.calls = 0
        self.inputs: list[EmbeddingInput] = []

    async def embed(self, inputs: Sequence[EmbeddingInput]) -> list[list[float]]:
        self.calls += 1
        self.inputs.extend(inputs)
        return [[float(len(item.text))] * 4 for item in inputs]


class ScriptedExtractors:
    """Proposes one PERSON and one USES edge per chunk when the chunk mentions Mavridis.

    Completion order is randomized per chunk so tests can prove stored order is stable.
    """

    def __init__(self, *, jitter: bool = False, fail_on: str | None = None) -> None:
        self.jitter = jitter
        self.fail_on = fail_on
        self.entity_calls = 0
        self.relationship_calls = 0
        self.max_in_flight = 0
        self._in_flight = 0

    async def _maybe_wait(self) -> None:
        self._in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self._in_flight)
        if self.jitter:
            await asyncio.sleep(random.uniform(0, 0.01))
        self._in_flight -= 1

    async def extract_entities(self, chunk: ExtractionInput) -> list[EntityCandidate]:
        self.entity_calls += 1
        if self.fail_on and self.fail_on in chunk.record_id:
            raise RuntimeError("model unavailable")
        await self._maybe_wait()
        name = "Alexandros Mavridis"
        start = chunk.text.find(name)
        if start < 0:
            return []
        return [EntityCandidate("PERSON", name, start, start + len(name), ("Alex",))]

    async def extract_relationships(
        self, chunk: ExtractionInput, known_entities: list[KnownEntity]
    ) -> list[RelationshipCandidate]:
        self.relationship_calls += 1
        await self._maybe_wait()
        quote = "He uses telephone +30 697 123 4567."
        start = chunk.text.find(quote)
        if start < 0 or not any(k.text == "Alexandros Mavridis" for k in known_entities):
            return []
        return [
            RelationshipCandidate(
                "USES",
                "PERSON",
                "Alexandros Mavridis",
                "PHONE",
                "+30 697 123 4567",
                quote,
                start,
                start + len(quote),
            )
        ]


@pytest.fixture
def plan(edition_dir: Path) -> IngestionPlan:
    return IngestionPlan(
        case_id=CASE,
        edition="en",
        edition_dir=edition_dir,
        fingerprint="fp-1",
        dataset_version="trg-synth-en-v1.0.0",
        embedding_model_id="fake-embed",
        chunking=ChunkingConfig(4000, 200),
        pipeline_version="ingestion@1",
    )


@pytest.fixture
def telemetry() -> tuple[TracerProvider, InMemorySpanExporter, MeterProvider, InMemoryMetricReader]:
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider(resource=Resource.create({}))
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(resource=Resource.create({}), metric_readers=[reader])
    return tracer_provider, exporter, meter_provider, reader


@pytest.fixture
def deps_factory(edition_dir: Path, telemetry: Any) -> Any:
    tracer_provider, _, meter_provider, _ = telemetry

    def build(
        *,
        systems: Sequence[str] = ("cdr", "docs"),
        extractors: ScriptedExtractors | None = None,
        store: FakeStore | None = None,
        receipts: MemoryReceipts | None = None,
    ) -> IngestionDependencies:
        store = store or FakeStore()
        extractors = extractors or ScriptedExtractors()
        return IngestionDependencies(
            sources=FakeSources(edition_dir, systems),
            store=store,
            ledger=store,
            receipts=receipts or MemoryReceipts(),
            embedder=FakeEmbedder(),
            entity_extractor=extractors,
            relationship_extractor=extractors,
            tracer=tracer_provider.get_tracer("t"),
            instruments=IngestionInstruments.create(meter_provider.get_meter("t")),
            clock=lambda: datetime(2026, 9, 2, tzinfo=UTC),
        )

    return build
