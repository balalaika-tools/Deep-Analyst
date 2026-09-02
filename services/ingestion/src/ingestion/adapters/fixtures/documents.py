"""Markdown documents: YAML front matter to the payload, the body to record text."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

from ingestion.domain.records import SourceBatch, SourceRecord, content_hash

SOURCE_SYSTEM = "docs"
RELATIVE_DIR = "raw/docs"
_DELIMITER = "---\n"


class DocumentParseError(ValueError):
    """A document lacks the front matter the record envelope needs."""


def split_front_matter(raw: str) -> tuple[dict[str, Any], str]:
    """Return (front matter, body). The body starts after the closing delimiter."""
    if not raw.startswith(_DELIMITER):
        raise DocumentParseError("document does not start with YAML front matter")
    end = raw.find("\n" + _DELIMITER, len(_DELIMITER))
    if end < 0:
        raise DocumentParseError("front matter is not closed")
    front_matter = yaml.safe_load(raw[len(_DELIMITER) : end + 1])
    if not isinstance(front_matter, dict):
        raise DocumentParseError("front matter must be a mapping")
    body = raw[end + 1 + len(_DELIMITER) :].lstrip("\n")
    return front_matter, body


def _event_time(front_matter: dict[str, Any]) -> tuple[datetime | None, str | None]:
    value = front_matter.get("document_date")
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC), value.isoformat()
    if isinstance(value, str) and value:
        parsed = date.fromisoformat(value)
        return datetime(parsed.year, parsed.month, parsed.day, tzinfo=UTC), value
    return None, None


def _record(path: Path, relative_path: str) -> SourceRecord:
    front_matter, body = split_front_matter(path.read_text(encoding="utf-8"))
    event_time, original = _event_time(front_matter)
    payload = {
        key: value.isoformat() if isinstance(value, date) else value
        for key, value in front_matter.items()
    }
    document_id = str(front_matter.get("document_id") or path.stem)
    return SourceRecord(
        source_system=SOURCE_SYSTEM,
        source_record_id=document_id,
        record_type="document",
        event_time_utc=event_time,
        original_time=original,
        text=body,
        payload=payload,
        source_path=relative_path,
        content_hash=content_hash({"document_id": document_id, **payload, "body": body}),
    )


def load_documents(edition_dir: Path) -> SourceBatch:
    directory = edition_dir / RELATIVE_DIR
    records = [
        _record(path, f"{RELATIVE_DIR}/{path.name}") for path in sorted(directory.glob("*.md"))
    ]
    return SourceBatch(source_system=SOURCE_SYSTEM, records=records)
