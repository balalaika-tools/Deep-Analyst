"""Carrier CDR rows to records and communication projections."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from ingestion.domain.normalization import normalize_imei, normalize_phone, to_utc
from ingestion.domain.records import (
    CommunicationProjection,
    SourceBatch,
    SourceRecord,
    content_hash,
)

SOURCE_SYSTEM = "cdr"
RELATIVE_PATH = "raw/cdr.csv"
_CHANNEL = {"MOC": "call", "MTC": "call", "SMS-MO": "sms", "SMS-MT": "sms"}
_DIRECTION = {"MOC": "out", "MTC": "in", "SMS-MO": "out", "SMS-MT": "in"}


def _record(row: dict[str, str]) -> tuple[SourceRecord, CommunicationProjection]:
    event_time = to_utc(row["ts_local"])
    imei = normalize_imei(row["imei"]) if row["imei"] else None
    calling = normalize_phone(row["calling_msisdn"])
    called = normalize_phone(row["called_msisdn"])
    payload: dict[str, Any] = {
        **row,
        "normalized": {
            "subscriber_msisdn": normalize_phone(row["subscriber_msisdn"]),
            "calling_msisdn": calling,
            "called_msisdn": called,
            "imei": imei,
            "event_time_utc": event_time.isoformat(),
        },
    }
    record = SourceRecord(
        source_system=SOURCE_SYSTEM,
        source_record_id=row["record_id"],
        record_type="cdr",
        event_time_utc=event_time,
        original_time=row["ts_local"],
        text=None,
        payload=payload,
        source_path=RELATIVE_PATH,
        content_hash=content_hash(dict(row)),
    )
    projection = CommunicationProjection(
        record_id=record.record_id,
        channel=_CHANNEL[row["record_type"]],
        direction=_DIRECTION[row["record_type"]],
        from_endpoint=calling,
        to_endpoint=called,
        from_field="calling_msisdn",
        to_field="called_msisdn",
        event_time_utc=event_time,
        original_time=row["ts_local"],
        duration_s=int(row["duration_s"]) if row["duration_s"] else None,
        device_id=imei,
    )
    return record, projection


def load_cdr(edition_dir: Path) -> SourceBatch:
    with (edition_dir / RELATIVE_PATH).open(encoding="utf-8", newline="") as handle:
        rows = [_record(row) for row in csv.DictReader(handle)]
    return SourceBatch(
        source_system=SOURCE_SYSTEM,
        records=[record for record, _ in rows],
        communications=[projection for _, projection in rows],
    )
