from datetime import UTC, datetime
from pathlib import Path

from ingestion.adapters.filesystem.receipt import FileReceiptStore
from ingestion.ports.ingestion_ledger import ChunkingConfig, Receipt, compute_fingerprint


def _fingerprint(**overrides: object) -> str:
    values: dict[str, object] = {
        "manifest_bytes": b'{"dataset_version": "v1"}',
        "embedding_model_id": "amazon.titan-embed-text-v2:0",
        "chunking": ChunkingConfig(window_chars=4000, overlap_chars=200),
        "pipeline_version": "ingestion@1",
    }
    values.update(overrides)
    return compute_fingerprint(**values)  # type: ignore[arg-type]


def test_fingerprint_changes_with_every_input_and_is_otherwise_stable() -> None:
    base = _fingerprint()
    assert base == _fingerprint()
    assert base != _fingerprint(manifest_bytes=b'{"dataset_version": "v2"}')
    assert base != _fingerprint(embedding_model_id="other-model")
    assert base != _fingerprint(chunking=ChunkingConfig(window_chars=400, overlap_chars=200))
    assert base != _fingerprint(chunking=ChunkingConfig(window_chars=4000, overlap_chars=50))
    assert base != _fingerprint(pipeline_version="ingestion@2")


def test_receipt_round_trips_and_absent_or_malformed_reads_as_none(tmp_path: Path) -> None:
    store = FileReceiptStore(tmp_path / "indexes")
    assert store.read("en") is None

    receipt = Receipt(
        fingerprint="abc",
        dataset_version="v1",
        embedding_model_id="m",
        chunking={"window_chars": 4000, "overlap_chars": 200},
        pipeline_version="ingestion@1",
        completed_at=datetime(2026, 9, 2, tzinfo=UTC),
        counts={"records": 142},
    )
    store.write("en", receipt)

    assert store.read("en") == receipt
    assert store.path_for("en").read_text().startswith("{\n")
    assert not list((tmp_path / "indexes").glob(".en-*"))
    store.path_for("en").write_text("not json")
    assert store.read("en") is None
