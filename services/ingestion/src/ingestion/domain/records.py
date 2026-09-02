"""Business nouns shared by adapters, rules, and persistence: records and projections."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

PROSE_RECORD_TYPES: frozenset[str] = frozenset({"document", "email", "extraction_message"})


def content_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """The common evidence envelope for one source item."""

    source_system: str
    source_record_id: str
    record_type: str
    event_time_utc: datetime | None
    original_time: str | None
    text: str | None
    payload: dict[str, Any]
    source_path: str
    content_hash: str

    @property
    def record_id(self) -> str:
        return f"{self.source_system}:{self.source_record_id}"

    @property
    def is_prose(self) -> bool:
        return self.record_type in PROSE_RECORD_TYPES and bool(self.text)


@dataclass(frozen=True, slots=True)
class CommunicationProjection:
    record_id: str
    channel: str
    direction: str
    from_endpoint: str
    to_endpoint: str
    from_field: str
    to_field: str
    event_time_utc: datetime
    original_time: str
    duration_s: int | None = None
    device_id: str | None = None


@dataclass(frozen=True, slots=True)
class AccountProjection:
    record_id: str
    account_id: str
    iban: str
    holder_name: str | None
    holder_type: str | None
    bic: str | None
    opened_date: date | None


@dataclass(frozen=True, slots=True)
class TransactionProjection:
    record_id: str
    txn_id: str
    booking_ts_utc: datetime
    value_date: date
    debtor_iban: str
    debtor_name: str | None
    creditor_iban: str
    creditor_name: str | None
    amount_minor: int
    amount_text: str
    currency: str
    status: str
    remittance_info: str | None


@dataclass(frozen=True, slots=True)
class SourceBatch:
    """Everything one adapter produces for one source system, in source order."""

    source_system: str
    records: list[SourceRecord]
    communications: list[CommunicationProjection] = field(default_factory=list)
    accounts: list[AccountProjection] = field(default_factory=list)
    transactions: list[TransactionProjection] = field(default_factory=list)
