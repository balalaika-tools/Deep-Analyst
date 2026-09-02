"""The ingestion use case: skip check, sources, rules, chunks, embeddings, extraction, persistence.

Persistence order is deterministic (records per source in source order, then chunks,
then the merged graph sorted by identifier) so concurrent extraction cannot change
what the store looks like afterwards.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from evidence_model import EntityDraft, RelationshipDraft
from observability import get_logger, start_genai_span, start_span, workflow_run
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import Link, SpanContext, Tracer
from opentelemetry.util.types import AttributeValue

from ingestion.domain import candidates as validation
from ingestion.domain.chunking import Chunk, chunk_text
from ingestion.domain.edges import (
    RuleOutput,
    account_edges,
    communication_edges,
    identifier_entities,
    transaction_edges,
)
from ingestion.domain.identifiers import find_identifiers
from ingestion.domain.records import SourceBatch, SourceRecord
from ingestion.observability.events import (
    SPAN_EXTRACT,
    SPAN_FINALIZE,
    SPAN_PERSIST_CHUNKS,
    SPAN_PERSIST_GRAPH,
    SPAN_RECORD,
    IngestionInstruments,
    LogEvent,
    Outcome,
    source_span_name,
)
from ingestion.ports.entity_extractor import EntityExtractor, ExtractionInput
from ingestion.ports.evidence_sources import EvidenceSources
from ingestion.ports.evidence_store import ChunkItem, EvidenceStore
from ingestion.ports.ingestion_ledger import (
    ChunkingConfig,
    Receipt,
    ReceiptStore,
    RunLedger,
    RunStart,
)
from ingestion.ports.relationship_extractor import KnownEntity, RelationshipExtractor
from ingestion.ports.text_embedder import EmbeddingInput, TextEmbedder

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class IngestionPlan:
    case_id: str
    edition: str
    edition_dir: Path
    fingerprint: str
    dataset_version: str
    embedding_model_id: str
    chunking: ChunkingConfig
    pipeline_version: str


@dataclass(slots=True)
class IngestionDependencies:
    sources: EvidenceSources
    store: EvidenceStore
    ledger: RunLedger
    receipts: ReceiptStore
    embedder: TextEmbedder
    entity_extractor: EntityExtractor
    relationship_extractor: RelationshipExtractor
    tracer: Tracer
    instruments: IngestionInstruments
    clock: Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class RunOutcome:
    outcome: str
    counts: dict[str, int]


@dataclass(slots=True)
class _ChunkResult:
    entities: list[EntityDraft] = field(default_factory=list)
    relationships: list[RelationshipDraft] = field(default_factory=list)
    entity_counts: Counter[str] = field(default_factory=Counter)
    relationship_counts: Counter[str] = field(default_factory=Counter)


@dataclass(slots=True)
class _RecordResult:
    chunks: list[ChunkItem] = field(default_factory=list)
    extractions: list[_ChunkResult] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _ChunkWork:
    record: SourceRecord
    chunk: Chunk
    chunk_index: int

    @property
    def chunk_id(self) -> str:
        return f"{self.record.record_id}#{self.chunk.char_start}-{self.chunk.char_end}"

    def embedding_input(self) -> EmbeddingInput:
        return EmbeddingInput(
            source_system=self.record.source_system,
            record_id=self.record.record_id,
            chunk_id=self.chunk_id,
            chunk_index=self.chunk_index,
            char_start=self.chunk.char_start,
            char_end=self.chunk.char_end,
            text=self.chunk.text,
        )


@dataclass(frozen=True, slots=True)
class _RecordWork:
    record: SourceRecord
    chunks: tuple[_ChunkWork, ...]


def _causal_links(coordinator_context: SpanContext) -> list[Link]:
    return [Link(coordinator_context)] if coordinator_context.is_valid else []


async def ingest_case(plan: IngestionPlan, deps: IngestionDependencies) -> RunOutcome:
    if await _already_ingested(plan, deps):
        log.info(LogEvent.RUN_SKIPPED, fingerprint=plan.fingerprint, edition=plan.edition)
        return RunOutcome(Outcome.SKIPPED, {})
    started_at = deps.clock()
    run_id = await deps.ledger.start(
        RunStart(
            case_id=plan.case_id,
            fingerprint=plan.fingerprint,
            dataset_version=plan.dataset_version,
            embedding_model_id=plan.embedding_model_id,
            started_at=started_at,
        )
    )
    with workflow_run(run_id):
        log.info(
            LogEvent.RUN_STARTED,
            workflow_run_id=run_id,
            fingerprint=plan.fingerprint,
            edition=plan.edition,
        )
        try:
            counts = await _run(plan, deps, run_id)
        except BaseException as exc:
            await deps.ledger.fail(run_id, completed_at=deps.clock(), error_type=type(exc).__name__)
            raise
        completed_at = deps.clock()
        await deps.ledger.complete(run_id, completed_at=completed_at, summary=counts)
        # The receipt is written last: its presence means the store is complete.
        deps.receipts.write(
            plan.edition,
            Receipt(
                fingerprint=plan.fingerprint,
                dataset_version=plan.dataset_version,
                embedding_model_id=plan.embedding_model_id,
                chunking={
                    "window_chars": plan.chunking.window_chars,
                    "overlap_chars": plan.chunking.overlap_chars,
                },
                pipeline_version=plan.pipeline_version,
                completed_at=completed_at,
                counts=counts,
            ),
        )
        log.info(LogEvent.RUN_COMPLETED, workflow_run_id=run_id, **counts)
        return RunOutcome(Outcome.SUCCESS, counts)


async def _already_ingested(plan: IngestionPlan, deps: IngestionDependencies) -> bool:
    receipt = deps.receipts.read(plan.edition)
    if receipt is None or receipt.fingerprint != plan.fingerprint:
        return False
    return await deps.ledger.has_completed(plan.case_id, plan.fingerprint)


async def _run(plan: IngestionPlan, deps: IngestionDependencies, run_id: str) -> dict[str, int]:
    rules = RuleOutput()
    records: list[SourceRecord] = []
    counts: Counter[str] = Counter()
    for source_system in deps.sources.source_systems:
        batch = await _load_source(source_system, plan, deps, rules)
        records.extend(batch.records)
        counts[f"records_{source_system}"] = len(batch.records)
    counts["records"] = len(records)

    record_work = _records_with_chunks(records, plan.chunking)
    coordinator_context = trace.get_current_span().get_span_context()
    record_results = await _process_records(record_work, plan, deps, coordinator_context)
    results = [item for record in record_results for item in record.extractions]
    for kind, key in (("entity", "entity_counts"), ("relationship", "relationship_counts")):
        merged: Counter[str] = Counter()
        for result in results:
            merged.update(getattr(result, key))
        deps.instruments.record_candidates(kind, merged)
        for outcome, count in merged.items():
            counts[f"{kind}_candidates_{outcome}"] = count
            if outcome != validation.ACCEPTED:
                log.info(LogEvent.CANDIDATE_REJECTED, kind=kind, outcome=outcome, count=count)

    chunk_items = [item for record in record_results for item in record.chunks]
    counts["chunks"] = await _finalize(
        run_id=run_id,
        coordinator_context=coordinator_context,
        chunk_items=chunk_items,
        rule_entities=rules.entities,
        rule_relationships=rules.relationships,
        extraction_results=results,
        deps=deps,
        counts=counts,
    )
    per_source: Counter[str] = Counter(
        record.record.source_system for record in record_work for _ in record.chunks
    )
    for source_system, count in per_source.items():
        deps.instruments.record_chunks(source_system, count)
    return dict(counts)


async def _load_source(
    source_system: str, plan: IngestionPlan, deps: IngestionDependencies, rules: RuleOutput
) -> SourceBatch:
    attributes = {"app.ingestion.source_system": source_system}
    with start_span(source_span_name(source_system), tracer=deps.tracer, attributes=attributes):
        batch = await deps.sources.load(source_system)
        await deps.store.persist_source(batch)
        for comm in batch.communications:
            rules.extend(communication_edges(comm))
        for account in batch.accounts:
            rules.extend(account_edges(account))
        for txn in batch.transactions:
            rules.extend(transaction_edges(txn, find_identifiers(txn.remittance_info or "")))
    log.info(LogEvent.SOURCE_LOADED, source_system=source_system, records=len(batch.records))
    return batch


def _records_with_chunks(records: list[SourceRecord], config: ChunkingConfig) -> list[_RecordWork]:
    grouped: list[_RecordWork] = []
    for record in records:
        if not record.text:
            continue
        chunks = tuple(
            _ChunkWork(record, chunk, chunk_index)
            for chunk_index, chunk in enumerate(
                chunk_text(
                    record.text,
                    window_chars=config.window_chars,
                    overlap_chars=config.overlap_chars,
                )
            )
        )
        if chunks:
            grouped.append(_RecordWork(record, chunks))
    return grouped


async def _process_records(
    records: list[_RecordWork],
    plan: IngestionPlan,
    deps: IngestionDependencies,
    coordinator_context: SpanContext,
) -> list[_RecordResult]:
    """Run each independently diagnosable record attempt in its own linked trace."""
    async with asyncio.TaskGroup() as group:
        tasks = [
            group.create_task(_process_record(record, plan, deps, coordinator_context))
            for record in records
        ]
    return [task.result() for task in tasks]


async def _process_record(
    work: _RecordWork,
    plan: IngestionPlan,
    deps: IngestionDependencies,
    coordinator_context: SpanContext,
) -> _RecordResult:
    attributes: dict[str, AttributeValue] = {
        "app.ingestion.record_id": work.record.record_id,
        "app.ingestion.source_system": work.record.source_system,
        "app.ingestion.chunk_count": len(work.chunks),
        "app.workflow.attempt": 1,
    }
    with start_genai_span(
        SPAN_RECORD,
        tracer=deps.tracer,
        attributes=attributes,
        context=otel_context.Context(),
        links=_causal_links(coordinator_context),
    ) as span:
        try:
            vectors = await deps.embedder.embed([chunk.embedding_input() for chunk in work.chunks])
            chunk_items = [
                (chunk.record, chunk.chunk, vector)
                for chunk, vector in zip(work.chunks, vectors, strict=True)
            ]
            extractions = await _extract_record(work, plan, deps)
        except BaseException:
            span.set_attribute("app.outcome", Outcome.ERROR)
            raise
        span.set_attribute("app.outcome", Outcome.SUCCESS)
        return _RecordResult(chunks=chunk_items, extractions=extractions)


async def _extract_record(
    work: _RecordWork, plan: IngestionPlan, deps: IngestionDependencies
) -> list[_ChunkResult]:
    if not work.record.is_prose:
        return []
    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(_extract_chunk(chunk, plan, deps)) for chunk in work.chunks]
    return [task.result() for task in tasks]


async def _finalize(
    *,
    run_id: str,
    coordinator_context: SpanContext,
    chunk_items: list[ChunkItem],
    rule_entities: list[EntityDraft],
    rule_relationships: list[RelationshipDraft],
    extraction_results: list[_ChunkResult],
    deps: IngestionDependencies,
    counts: Counter[str],
) -> int:
    attributes: dict[str, AttributeValue] = {
        "app.workflow.run.id": run_id,
        "app.ingestion.chunk_count": len(chunk_items),
    }
    with start_span(
        SPAN_FINALIZE,
        tracer=deps.tracer,
        attributes=attributes,
        context=otel_context.Context(),
        links=_causal_links(coordinator_context),
    ) as span:
        try:
            with start_span(SPAN_PERSIST_CHUNKS, tracer=deps.tracer):
                stored = await deps.store.persist_chunks(chunk_items)
            entities = _merge_entities(
                rule_entities,
                [entity for result in extraction_results for entity in result.entities],
            )
            relationships = rule_relationships + [
                relationship
                for result in extraction_results
                for relationship in result.relationships
            ]
            with start_span(SPAN_PERSIST_GRAPH, tracer=deps.tracer):
                counts["entities"], counts["relationships"] = await deps.store.persist_graph(
                    entities, relationships
                )
        except BaseException:
            span.set_attribute("app.outcome", Outcome.ERROR)
            raise
        span.set_attribute("app.outcome", Outcome.SUCCESS)
    return stored


async def _extract_chunk(
    work: _ChunkWork, plan: IngestionPlan, deps: IngestionDependencies
) -> _ChunkResult:
    record, chunk = work.record, work.chunk
    spans = find_identifiers(chunk.text)
    shifted = [
        span.__class__(
            span.entity_type,
            span.raw,
            span.normalized_key,
            chunk.char_start + span.char_start,
            chunk.char_start + span.char_end,
        )
        for span in spans
    ]
    rule_entities = identifier_entities(plan.case_id, record.record_id, shifted)
    context = validation.ChunkContext(
        case_id=plan.case_id, record_id=record.record_id, chunk=chunk, rule_entities=rule_entities
    )
    attributes: dict[str, AttributeValue] = {
        "gen_ai.operation.name": "invoke_workflow",
        "gen_ai.workflow.name": "extract_chunk",
        "app.ingestion.record_id": record.record_id,
        "app.ingestion.source_system": record.source_system,
        "app.ingestion.chunk_id": work.chunk_id,
        "app.ingestion.chunk_index": work.chunk_index,
        "app.ingestion.chunk_char_start": chunk.char_start,
        "app.ingestion.chunk_char_end": chunk.char_end,
    }
    with start_genai_span(SPAN_EXTRACT, tracer=deps.tracer, attributes=attributes):
        chunk_input = ExtractionInput(record_id=record.record_id, text=chunk.text)
        entity_candidates = await deps.entity_extractor.extract_entities(chunk_input)
        entities = validation.validate_entity_candidates(context, entity_candidates)
        aliases = validation.alias_map(entity_candidates, entities.accepted)
        known = [
            KnownEntity(e.entity_type.value, e.label, aliases.get(e.entity_id, ()))
            for e in entities.accepted
        ] + [KnownEntity(e.entity_type.value, e.label) for e in rule_entities]
        relationship_candidates = await deps.relationship_extractor.extract_relationships(
            chunk_input, known
        )
        relationships = validation.validate_relationship_candidates(
            context,
            relationship_candidates,
            validation.EntityIndex(context, entities.accepted, aliases),
        )
    return _ChunkResult(
        entities=rule_entities + entities.accepted,
        relationships=relationships.accepted,
        entity_counts=entities.counts,
        relationship_counts=relationships.counts,
    )


def _merge_entities(*groups: list[EntityDraft]) -> list[EntityDraft]:
    """One draft per identifier with the union of its evidence, in first-seen order."""
    merged: dict[str, EntityDraft] = {}
    for group in groups:
        for entity in group:
            existing = merged.get(entity.entity_id)
            merged[entity.entity_id] = (
                existing.with_refs(entity.source_refs) if existing else entity
            )
    return list(merged.values())
