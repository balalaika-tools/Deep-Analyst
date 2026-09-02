from typing import Any

import pytest
from evidence_model import Predicate, RelationshipStatus
from ingestion.application.ingest_dataset import IngestionPlan, ingest_dataset
from ingestion.observability.events import Outcome


@pytest.mark.asyncio
async def test_first_run_loads_sources_indexes_chunks_extracts_and_writes_the_receipt_last(
    plan: IngestionPlan, deps_factory: Any, telemetry: Any
) -> None:
    deps = deps_factory()
    _, exporter, _, _ = telemetry

    outcome = await ingest_dataset(plan, deps)

    store = deps.store
    assert outcome.outcome == Outcome.SUCCESS
    assert store.sources == ["cdr", "docs"]
    assert outcome.counts["records"] == 65 and outcome.counts["chunks"] == 10
    assert len(store.chunks) == 10 and all(len(vector) == 4 for _, _, vector in store.chunks)
    assert deps.embedder.calls == 10
    assert [item.record_id for item in deps.embedder.inputs] == [
        record.record_id for record, _, _ in store.chunks
    ]
    assert [item.chunk_id for item in deps.embedder.inputs] == [
        f"{record.record_id}#{chunk.char_start}-{chunk.char_end}"
        for record, chunk, _ in store.chunks
    ]
    assert (
        deps.entity_extractor.entity_calls == 10
        and deps.relationship_extractor.relationship_calls == 10
    )

    phone = next(e for e in store.entities if e.entity_id == "PHONE:306971234567")
    kinds = {ref.locator.kind for ref in phone.source_refs}
    assert kinds == {"field", "text_span"}, "one PHONE entity with evidence from CDR and R-01"
    proposed = [r for r in store.relationships if r.status is RelationshipStatus.PROPOSED]
    assert [(r.predicate, r.subject.entity_id, r.object.entity_id) for r in proposed] == [
        (Predicate.USES, "PERSON:docs:R-01:alexandros-mavridis", "PHONE:306971234567")
    ]
    assert all(
        r.status is RelationshipStatus.CONFIRMED for r in store.relationships if r not in proposed
    )
    assert outcome.counts["entity_candidates_accepted"] == 1
    assert outcome.counts["relationship_candidates_accepted"] == 1

    receipt = deps.receipts.read("en")
    assert (
        receipt is not None and receipt.fingerprint == "fp-1" and receipt.counts == outcome.counts
    )
    assert store.runs["run-1"]["outcome"] == "completed"
    names = [span.name for span in exporter.get_finished_spans()]
    assert names.count("load cdr") == 1 and names.count("load docs") == 1
    assert names.count("ingest record") == 10
    assert names.count("invoke_workflow extract_chunk") == 10
    assert names.count("finalize ingestion") == 1
    assert names[-1] == "finalize ingestion"


@pytest.mark.asyncio
async def test_matching_receipt_and_completed_ledger_row_skip_all_work(
    plan: IngestionPlan, deps_factory: Any
) -> None:
    deps = deps_factory()
    await ingest_dataset(plan, deps)
    second = deps_factory(store=deps.store, receipts=deps.receipts)

    outcome = await ingest_dataset(plan, second)

    assert outcome.outcome == Outcome.SKIPPED
    assert second.sources.loaded == [] and second.embedder.calls == 0
    assert second.entity_extractor.entity_calls == 0
    assert len(deps.store.runs) == 1


@pytest.mark.asyncio
async def test_receipt_without_a_completed_ledger_row_re_runs(
    plan: IngestionPlan, deps_factory: Any
) -> None:
    deps = deps_factory()
    await ingest_dataset(plan, deps)
    fresh_store = deps_factory(receipts=deps.receipts)

    outcome = await ingest_dataset(plan, fresh_store)

    assert outcome.outcome == Outcome.SUCCESS
    assert fresh_store.sources.loaded == ["cdr", "docs"]


@pytest.mark.asyncio
async def test_failure_leaves_a_failed_ledger_row_and_no_receipt(
    plan: IngestionPlan, deps_factory: Any, telemetry: Any
) -> None:
    deps = deps_factory(extractors=type(deps_factory().entity_extractor)(fail_on="R-03"))

    with pytest.raises(ExceptionGroup):
        await ingest_dataset(plan, deps)

    assert deps.receipts.read("en") is None
    assert deps.store.runs["run-1"]["outcome"] == "failed"
    assert deps.store.runs["run-1"]["error_type"] == "ExceptionGroup"
    assert deps.store.entities == []


@pytest.mark.asyncio
async def test_stored_graph_is_identical_regardless_of_task_completion_order(
    plan: IngestionPlan, deps_factory: Any
) -> None:
    extractor_type = type(deps_factory().entity_extractor)
    baseline = deps_factory(extractors=extractor_type(jitter=False))
    await ingest_dataset(plan, baseline)

    for _ in range(3):
        jittered = deps_factory(extractors=extractor_type(jitter=True))
        await ingest_dataset(plan, jittered)
        assert [e.entity_id for e in jittered.store.entities] == [
            e.entity_id for e in baseline.store.entities
        ]
        assert [r.relationship_id for r in jittered.store.relationships] == [
            r.relationship_id for r in baseline.store.relationships
        ]
        assert [ref for e in jittered.store.entities for ref in e.source_refs] == [
            ref for e in baseline.store.entities for ref in e.source_refs
        ]
