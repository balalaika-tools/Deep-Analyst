"""Trusted reads over canonical global evidence tables and search indexes."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from evidence_model import (
    EntityType,
    FieldLocator,
    Predicate,
    RelationshipStatus,
    SourceRef,
    TextSpanLocator,
)
from pydantic import ValidationError

from investigation_agent.adapters.postgres.pools import AgentPool
from investigation_agent.genai.evidence_search.schemas import (
    RetrievalCandidate,
    RetrievalModality,
    RetrievalQuery,
)
from investigation_agent.genai.investigation.connections import (
    ConnectionFilters,
    GraphEdge,
    GraphNode,
    ResolvedSourceRef,
)


@dataclass(frozen=True, slots=True)
class _RecordEvidence:
    record_id: str
    content_hash: str
    text: str | None
    payload: Mapping[str, object]


class PostgresEvidenceReader:
    """Single concrete adapter satisfying search and graph capability protocols."""

    def __init__(
        self,
        pool: AgentPool,
        *,
        acquisition_timeout_s: float = 2.0,
        statement_timeout_ms: int = 5_000,
    ) -> None:
        if acquisition_timeout_s <= 0 or statement_timeout_ms < 1:
            raise ValueError("evidence reader timeouts must be positive")
        self._pool = pool
        self._acquisition_timeout_s = acquisition_timeout_s
        self._statement_timeout_ms = statement_timeout_ms

    async def search_lexical(
        self,
        *,
        query: RetrievalQuery,
        excluded_chunk_ids: frozenset[str],
        deadline: float,
    ) -> Sequence[RetrievalCandidate]:
        clauses, filter_parameters, next_position = _chunk_filters(
            query=query,
            excluded_chunk_ids=excluded_chunk_ids,
            first_position=2,
        )
        limit_position = next_position
        statement = (
            "SELECT c.chunk_id, c.record_id, c.char_start, c.char_end, "
            "c.text, c.source_system, c.event_time_utc, r.content_hash, r.text AS record_text, "
            "paradedb.score(c.chunk_id) AS score "
            "FROM public.chunks AS c "
            "JOIN public.records AS r ON r.record_id = c.record_id "
            "WHERE c.text @@@ paradedb.match('text', $1, conjunction_mode => true) "
            f"{clauses} ORDER BY score DESC, c.chunk_id ASC LIMIT ${limit_position}"
        )
        rows = await self._fetch_rows(
            statement,
            (query.query, *filter_parameters, query.top_k),
            deadline=deadline,
        )
        return _retrieval_candidates(rows, modality=RetrievalModality.BM25)

    async def search_vector(
        self,
        *,
        query: RetrievalQuery,
        embedding: Sequence[float],
        excluded_chunk_ids: frozenset[str],
        deadline: float,
    ) -> Sequence[RetrievalCandidate]:
        vector = _vector_literal(embedding)
        clauses, filter_parameters, next_position = _chunk_filters(
            query=query,
            excluded_chunk_ids=excluded_chunk_ids,
            first_position=2,
        )
        limit_position = next_position
        statement = (
            "SELECT c.chunk_id, c.record_id, c.char_start, c.char_end, "
            "c.text, c.source_system, c.event_time_utc, r.content_hash, r.text AS record_text, "
            "1.0 - (c.embedding <=> $1::public.vector) AS score "
            "FROM public.chunks AS c "
            "JOIN public.records AS r ON r.record_id = c.record_id "
            "WHERE c.embedding IS NOT NULL "
            f"{clauses} ORDER BY c.embedding <=> $1::public.vector, c.chunk_id ASC "
            f"LIMIT ${limit_position}"
        )
        rows = await self._fetch_rows(
            statement,
            (vector, *filter_parameters, query.top_k),
            deadline=deadline,
        )
        return _retrieval_candidates(rows, modality=RetrievalModality.VECTOR)

    async def load_graph_entities(
        self,
        *,
        entity_ids: frozenset[str],
        row_limit: int,
        deadline: float,
    ) -> tuple[GraphNode, ...]:
        if not entity_ids or row_limit < 1:
            return ()
        async with self._pool.connection(
            timeout=min(self._acquisition_timeout_s, _remaining(deadline))
        ) as connection:
            async with connection.transaction():
                async with connection.cursor() as cursor:
                    await _set_read_controls(
                        cursor,
                        statement_timeout_ms=self._statement_timeout_ms,
                    )
                    await cursor.execute(
                        "SELECT entity_id, entity_type, label, source_refs "
                        "FROM public.entities "
                        "WHERE entity_id = ANY($1::text[]) "
                        "ORDER BY entity_id LIMIT $2",
                        (sorted(entity_ids), row_limit),
                    )
                    rows = await cursor.fetchall()
                    references = _all_references(rows)
                    records = await _load_records(
                        cursor,
                        references=references,
                    )
        nodes: list[GraphNode] = []
        for row in rows:
            sources = _resolve_references(
                _parse_references(row.get("source_refs")),
                records=records,
            )
            if not sources:
                continue
            nodes.append(
                GraphNode(
                    entity_id=str(row["entity_id"]),
                    entity_type=EntityType(str(row["entity_type"])),
                    label=str(row["label"]),
                    sources=sources,
                )
            )
        return tuple(sorted(nodes, key=lambda item: item.entity_id))

    async def load_graph_edges(
        self,
        *,
        frontier_entity_ids: frozenset[str],
        filters: ConnectionFilters,
        row_limit: int,
        deadline: float,
    ) -> tuple[GraphEdge, ...]:
        if not frontier_entity_ids or row_limit < 1:
            return ()
        clauses, parameters, next_position = _graph_filters(filters, first_position=3)
        statement = (
            "SELECT relationship_id, subject_entity_id, predicate, object_entity_id, "
            "status, occurred_at, source_refs FROM public.relationships "
            "WHERE (subject_entity_id = ANY($1::text[]) OR object_entity_id = ANY($1::text[])) "
            f"{clauses} ORDER BY relationship_id LIMIT ${next_position}"
        )
        async with self._pool.connection(
            timeout=min(self._acquisition_timeout_s, _remaining(deadline))
        ) as connection:
            async with connection.transaction():
                async with connection.cursor() as cursor:
                    await _set_read_controls(
                        cursor,
                        statement_timeout_ms=self._statement_timeout_ms,
                    )
                    await cursor.execute(
                        statement,
                        (
                            sorted(frontier_entity_ids),
                            [status.value for status in filters.statuses],
                            *parameters,
                            row_limit,
                        ),
                    )
                    rows = await cursor.fetchall()
                    references = _all_references(rows)
                    records = await _load_records(
                        cursor,
                        references=references,
                    )
        edges: list[GraphEdge] = []
        for row in rows:
            sources = _resolve_references(
                _parse_references(row.get("source_refs")),
                records=records,
            )
            if not sources:
                continue
            edges.append(
                GraphEdge(
                    relationship_id=str(row["relationship_id"]),
                    subject_entity_id=str(row["subject_entity_id"]),
                    predicate=Predicate(str(row["predicate"])),
                    object_entity_id=str(row["object_entity_id"]),
                    status=RelationshipStatus(str(row["status"])),
                    occurred_at=row.get("occurred_at")
                    if isinstance(row.get("occurred_at"), datetime)
                    else None,
                    sources=sources,
                )
            )
        return tuple(sorted(edges, key=lambda item: item.relationship_id))

    async def _fetch_rows(
        self,
        statement: str,
        parameters: tuple[object, ...],
        *,
        deadline: float,
    ) -> Sequence[Mapping[str, object]]:
        async with self._pool.connection(
            timeout=min(self._acquisition_timeout_s, _remaining(deadline))
        ) as connection:
            async with connection.transaction():
                async with connection.cursor() as cursor:
                    await _set_read_controls(
                        cursor,
                        statement_timeout_ms=self._statement_timeout_ms,
                    )
                    await cursor.execute(statement, parameters)
                    return await cursor.fetchall()


def _chunk_filters(
    *,
    query: RetrievalQuery,
    excluded_chunk_ids: frozenset[str],
    first_position: int,
) -> tuple[str, tuple[object, ...], int]:
    clauses: list[str] = []
    parameters: list[object] = []
    position = first_position
    if query.source_systems:
        clauses.append(f"AND c.source_system = ANY(${position}::text[]) ")
        parameters.append(sorted(set(query.source_systems)))
        position += 1
    if query.event_time_from is not None:
        clauses.append(f"AND c.event_time_utc >= ${position} ")
        parameters.append(query.event_time_from)
        position += 1
    if query.event_time_to is not None:
        clauses.append(f"AND c.event_time_utc <= ${position} ")
        parameters.append(query.event_time_to)
        position += 1
    if excluded_chunk_ids:
        clauses.append(f"AND NOT (c.chunk_id = ANY(${position}::text[])) ")
        parameters.append(sorted(excluded_chunk_ids))
        position += 1
    return "".join(clauses), tuple(parameters), position


def _graph_filters(
    filters: ConnectionFilters,
    *,
    first_position: int,
) -> tuple[str, tuple[object, ...], int]:
    clauses = ["AND status = ANY($2::text[]) "]
    parameters: list[object] = []
    position = first_position
    if filters.predicates:
        clauses.append(f"AND predicate = ANY(${position}::text[]) ")
        parameters.append([predicate.value for predicate in filters.predicates])
        position += 1
    if filters.occurred_from is not None:
        clauses.append(f"AND occurred_at >= ${position} ")
        parameters.append(filters.occurred_from)
        position += 1
    if filters.occurred_to is not None:
        clauses.append(f"AND occurred_at <= ${position} ")
        parameters.append(filters.occurred_to)
        position += 1
    return "".join(clauses), tuple(parameters), position


async def _set_read_controls(
    cursor: Any,
    *,
    statement_timeout_ms: int,
) -> None:
    await cursor.execute("SET TRANSACTION READ ONLY")
    await cursor.execute(
        "SELECT set_config('statement_timeout', $1, true)",
        (f"{statement_timeout_ms}ms",),
    )


def _retrieval_candidates(
    rows: Sequence[Mapping[str, object]],
    *,
    modality: RetrievalModality,
) -> tuple[RetrievalCandidate, ...]:
    candidates: list[RetrievalCandidate] = []
    for rank, row in enumerate(rows, start=1):
        text = str(row["text"])
        record_text = row.get("record_text")
        raw_start = row["char_start"]
        raw_end = row["char_end"]
        raw_score = row["score"]
        if not isinstance(raw_start, int) or not isinstance(raw_end, int):
            raise EvidenceIntegrityError("chunk offsets are not integers")
        if not isinstance(raw_score, int | float | Decimal):
            raise EvidenceIntegrityError("retrieval score is not numeric")
        start = raw_start
        end = raw_end
        if not isinstance(record_text, str):
            raise EvidenceIntegrityError("retrieval result has no source record text")
        if record_text[start:end] != text:
            raise EvidenceIntegrityError("chunk offsets do not resolve against the source record")
        record_id = str(row["record_id"])
        candidates.append(
            RetrievalCandidate(
                chunk_id=str(row["chunk_id"]),
                record_id=record_id,
                text=text,
                content_hash=str(row["content_hash"]),
                source_ref=SourceRef(
                    record_id=record_id,
                    locator=TextSpanLocator(
                        field="text",
                        char_start=start,
                        char_end=end,
                        quote=text,
                    ),
                ),
                source_system=str(row["source_system"]),
                event_time_utc=(
                    row.get("event_time_utc")
                    if isinstance(row.get("event_time_utc"), datetime)
                    else None
                ),
                modality=modality,
                raw_score=float(raw_score),
                rank=rank,
            )
        )
    return tuple(candidates)


def _all_references(rows: Sequence[Mapping[str, object]]) -> tuple[SourceRef, ...]:
    return tuple(ref for row in rows for ref in _parse_references(row.get("source_refs")))


async def _load_records(
    cursor: Any,
    *,
    references: tuple[SourceRef, ...],
) -> dict[str, _RecordEvidence]:
    ids = sorted({ref.record_id for ref in references})
    if not ids:
        return {}
    await cursor.execute(
        "SELECT record_id, content_hash, text, payload FROM public.records "
        "WHERE record_id = ANY($1::text[]) ORDER BY record_id",
        (ids,),
    )
    records: dict[str, _RecordEvidence] = {}
    for row in await cursor.fetchall():
        record_id = str(row["record_id"])
        payload = row.get("payload")
        records[record_id] = _RecordEvidence(
            record_id=record_id,
            content_hash=str(row["content_hash"]),
            text=str(row["text"]) if row.get("text") is not None else None,
            payload=payload if isinstance(payload, Mapping) else {},
        )
    return records


def _parse_references(value: object) -> tuple[SourceRef, ...]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise EvidenceIntegrityError("stored source references are malformed") from error
    if not isinstance(value, list):
        return ()
    try:
        return tuple(SourceRef.model_validate(item) for item in value)
    except ValidationError as error:
        raise EvidenceIntegrityError(
            "stored source references violate the shared schema"
        ) from error


def _resolve_references(
    references: tuple[SourceRef, ...],
    *,
    records: Mapping[str, _RecordEvidence],
) -> tuple[ResolvedSourceRef, ...]:
    resolved: list[ResolvedSourceRef] = []
    for reference in references:
        record = records.get(reference.record_id)
        if record is None or not _locator_matches(reference, record):
            return ()
        resolved.append(
            ResolvedSourceRef(
                content_hash=record.content_hash,
                source_ref=reference,
            )
        )
    return tuple(resolved)


def _locator_matches(reference: SourceRef, record: _RecordEvidence) -> bool:
    locator = reference.locator
    if isinstance(locator, FieldLocator):
        return locator.field in {"text", "payload"} or locator.field in record.payload
    if not isinstance(locator, TextSpanLocator):
        return False
    if locator.field == "text":
        return record.text is not None and locator.matches(record.text)
    candidate = record.payload.get(locator.field)
    return isinstance(candidate, str) and locator.matches(candidate)


def _vector_literal(embedding: Sequence[float]) -> str:
    if not embedding:
        raise ValueError("embedding cannot be empty")
    values = [float(value) for value in embedding]
    if any(not math.isfinite(value) for value in values):
        raise ValueError("embedding must contain only finite values")
    return "[" + ",".join(format(value, ".17g") for value in values) + "]"


def _remaining(deadline: float) -> float:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise TimeoutError("evidence read deadline exhausted")
    return remaining


class EvidenceIntegrityError(ValueError):
    pass


__all__ = ["EvidenceIntegrityError", "PostgresEvidenceReader"]
