from datetime import UTC, datetime
from pathlib import Path

import pytest
from ingestion.adapters.fixtures.documents import (
    DocumentParseError,
    load_documents,
    split_front_matter,
)

CASE = "case_trg_001"


def test_documents_yield_10_records_and_body_excludes_front_matter(edition_dir: Path) -> None:
    batch = load_documents(edition_dir, CASE)

    assert len(batch.records) == 10 and not batch.communications
    r01 = next(record for record in batch.records if record.source_record_id == "R-01")
    assert r01.text is not None
    assert "document_id" not in r01.text and "---" not in r01.text
    assert r01.text.startswith("> Synthetic test fixture")
    assert "+30 697 123 4567" in r01.text
    assert r01.payload["genre"] == "surveillance_report"
    assert r01.payload["document_date"] == "2026-03-06"
    assert r01.event_time_utc == datetime(2026, 3, 6, tzinfo=UTC)
    assert r01.original_time == "2026-03-06"
    assert r01.source_path == "raw/docs/R-01.md"


def test_front_matter_must_be_present_and_closed() -> None:
    with pytest.raises(DocumentParseError):
        split_front_matter("# no front matter\n")
    with pytest.raises(DocumentParseError):
        split_front_matter("---\nkey: value\n")
    front, body = split_front_matter("---\nkey: value\n---\n\nBody\n")
    assert front == {"key": "value"} and body == "Body\n"
