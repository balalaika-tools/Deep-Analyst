"""Opt-in compatibility check against Bedrock: `uv run pytest -m live services/ingestion/tests/live`.

Asserts shape, allowed types, and bounded latency only; never prose. Requires the same
environment the service needs (DATABASE_URL is not used but must parse).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from ingestion.adapters.fixtures.documents import load_documents
from ingestion.config.settings import SettingsError, load_settings
from ingestion.genai.entity_extraction.agent import build_entity_agent
from ingestion.genai.entity_extraction.extractor import AgentEntityExtractor
from ingestion.genai.entity_extraction.llm import build_chat_model
from ingestion.genai.relationship_extraction.agent import build_relationship_agent
from ingestion.genai.relationship_extraction.extractor import AgentRelationshipExtractor
from ingestion.genai.shared.throttle import build_throttle
from ingestion.ports.entity_extractor import ExtractionInput
from ingestion.ports.relationship_extractor import KnownEntity
from opentelemetry import trace

pytestmark = pytest.mark.live
MAX_SECONDS = 90.0
ALLOWED_ENTITY_TYPES = {"PERSON", "ORGANIZATION", "LOCATION"}
ALLOWED_PREDICATES = {"USES", "ASSOCIATED_WITH", "DIRECTOR_OF", "KIN_OF"}


@pytest.mark.asyncio
async def test_both_extractors_return_well_formed_candidates_for_r01(edition_dir: Path) -> None:
    try:
        settings = load_settings()
    except SettingsError as exc:
        pytest.fail(f"live test needs the full ingestion environment: {exc}")
    r01 = next(
        r
        for r in load_documents(edition_dir, "case_trg_001").records
        if r.source_record_id == "R-01"
    )
    assert r01.text is not None
    throttle = build_throttle(settings)
    model = build_chat_model(settings, rate_limiter=throttle.rate_limiter, callbacks=[])
    tracer = trace.get_tracer("live")
    entity_extractor = AgentEntityExtractor(
        build_entity_agent(model, max_retries=settings.llm_max_retries),
        throttle=throttle,
        tracer=tracer,
    )
    relationship_extractor = AgentRelationshipExtractor(
        build_relationship_agent(model, max_retries=settings.llm_max_retries),
        throttle=throttle,
        tracer=tracer,
    )
    chunk = ExtractionInput(record_id=r01.record_id, text=r01.text)

    started = time.perf_counter()
    entities = await entity_extractor.extract_entities(chunk)
    known = [KnownEntity(e.entity_type, e.text, e.aliases) for e in entities]
    known.append(KnownEntity("PHONE", "+30 697 123 4567"))
    relationships = await relationship_extractor.extract_relationships(chunk, known)
    elapsed = time.perf_counter() - started

    assert elapsed < MAX_SECONDS
    assert entities, "R-01 names at least one person"
    assert {e.entity_type for e in entities} <= ALLOWED_ENTITY_TYPES
    assert all(
        r01.text[e.char_start : e.char_end] == e.text or e.text in r01.text for e in entities
    )
    assert {r.predicate for r in relationships} <= ALLOWED_PREDICATES
    assert all(r.quote for r in relationships)
