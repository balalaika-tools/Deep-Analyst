"""Device-extraction messages (JSON lines) to records and communication projections."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ingestion.domain.normalization import normalize_imei, normalize_phone, to_utc
from ingestion.domain.records import (
    CommunicationProjection,
    SourceBatch,
    SourceRecord,
    content_hash,
)

SOURCE_SYSTEM = "extraction"
RELATIVE_PATH = "raw/extraction.jsonl"


def _record(case_id: str, row: dict[str, Any]) -> tuple[SourceRecord, CommunicationProjection]:
    event_time = to_utc(str(row["ts_utc"]))
    subscriber = normalize_phone(str(row["subscriber_msisdn"]))
    peer = normalize_phone(str(row["peer"]))
    imei = normalize_imei(str(row["imei"])) if row.get("imei") else None
    outbound = row["direction"] == "out"
    payload: dict[str, Any] = {
        **row,
        "normalized": {
            "subscriber_msisdn": subscriber,
            "peer": peer,
            "imei": imei,
            "event_time_utc": event_time.isoformat(),
        },
    }
    body = row.get("body")
    record = SourceRecord(
        case_id=case_id,
        source_system=SOURCE_SYSTEM,
        source_record_id=str(row["msg_id"]),
        record_type="extraction_message",
        event_time_utc=event_time,
        original_time=str(row["ts_utc"]),
        text=str(body) if body else None,
        payload=payload,
        source_path=RELATIVE_PATH,
        content_hash=content_hash(row),
    )
    projection = CommunicationProjection(
        record_id=record.record_id,
        case_id=case_id,
        channel=str(row["app"]),
        direction=str(row["direction"]),
        from_endpoint=subscriber if outbound else peer,
        to_endpoint=peer if outbound else subscriber,
        from_field="subscriber_msisdn" if outbound else "peer",
        to_field="peer" if outbound else "subscriber_msisdn",
        event_time_utc=event_time,
        original_time=str(row["ts_utc"]),
        device_id=imei,
    )
    return record, projection


def load_extraction(edition_dir: Path, case_id: str) -> SourceBatch:
    lines = (edition_dir / RELATIVE_PATH).read_text(encoding="utf-8").splitlines()
    rows = [_record(case_id, json.loads(line)) for line in lines if line.strip()]
    return SourceBatch(
        source_system=SOURCE_SYSTEM,
        records=[record for record, _ in rows],
        communications=[projection for _, projection in rows],
    )
