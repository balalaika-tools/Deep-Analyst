"""Validate carrier/device-extraction invariants: CDR structure, cross-source
reconciliation, and background communications-profile controls."""

from datetime import datetime
from typing import Any

from dataset.core.fixtures import PHONES
from dataset.core.util import _digits, _require, _valid_imei


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _network_roles_from_cdr(row: dict[str, Any]) -> tuple[str, str]:
    return "+" + row["calling_msisdn"], "+" + row["called_msisdn"]


def _network_roles_from_extraction(row: dict[str, Any]) -> tuple[str, str]:
    if row["direction"] == "out":
        return row["subscriber_msisdn"], row["peer"]
    return row["peer"], row["subscriber_msisdn"]


def _reconciliation_candidates(
    extraction_row: dict[str, Any], cdr_rows: list[dict[str, Any]]
) -> list[str]:
    if extraction_row["app"] != "sms":
        return []
    target_roles = _network_roles_from_extraction(extraction_row)
    target_time = _parse_timestamp(extraction_row["ts_utc"])
    candidates = []
    for cdr_row in cdr_rows:
        if not cdr_row["record_type"].startswith("SMS"):
            continue
        if _network_roles_from_cdr(cdr_row) != target_roles:
            continue
        cdr_time = _parse_timestamp(cdr_row["ts_local"])
        if abs((cdr_time - target_time).total_seconds()) > 90:
            continue
        same_imei = bool(cdr_row["imei"]) and cdr_row["imei"] == extraction_row["imei"]
        same_subscriber = "+" + cdr_row["subscriber_msisdn"] == extraction_row["subscriber_msisdn"]
        if same_imei or same_subscriber:
            candidates.append(cdr_row["record_id"])
    return sorted(candidates)


def validate_cdr_structure(cdr: list[dict[str, Any]], extraction: list[dict[str, Any]]) -> None:
    all_imeis = {row["imei"] for row in cdr + extraction if row["imei"]}
    _require(all(_valid_imei(value) for value in all_imeis), "in-corpus IMEI checksum failure")
    _require(
        all(cdr[index]["seq"] < cdr[index + 1]["seq"] for index in range(54)),
        "CDR seq must increase",
    )
    _require(
        all(row["record_type"] in {"MOC", "MTC", "SMS-MO", "SMS-MT"} for row in cdr),
        "invalid CDR type",
    )
    for row in cdr:
        offset = _parse_timestamp(row["ts_local"]).utcoffset()
        _require(
            offset is not None and offset.total_seconds() == 7200,
            "CDR offset must be +02:00",
        )
        if row["record_type"].startswith("SMS"):
            _require(row["duration_s"] == "", "SMS must not have call duration")
        else:
            _require(20 <= int(row["duration_s"]) <= 900, "call duration outside 20..900 seconds")


def validate_reconciliation(
    cdr: list[dict[str, Any]], extraction: list[dict[str, Any]], transactions: list[dict[str, Any]]
) -> None:
    intended_pairs = {
        "X-204": "c01",
        "X-205": "c02",
        "X-206": "c04",
        "X-207": "c10",
        "X-303": "c12",
        "X-208": "c13",
    }
    by_message = {row["msg_id"]: row for row in extraction}
    for msg_id, cdr_id in intended_pairs.items():
        candidates = _reconciliation_candidates(by_message[msg_id], cdr)
        _require(candidates == [cdr_id], f"intended pair must reconcile exactly once: {msg_id}")
    _require(
        _reconciliation_candidates(by_message["X-301"], cdr) == [], "X-301 must remain unmatched"
    )
    _require(
        _reconciliation_candidates(by_message["X-302"], cdr) == [], "X-302 must remain unmatched"
    )
    _require(
        _reconciliation_candidates(by_message["X-N01"], cdr) == ["c15", "c16"],
        "background ambiguity control differs",
    )

    background_marina = [
        row for row in cdr if int(row["record_id"][1:]) >= 14 and row["cell_id"] == "MAR-20530-0091"
    ]
    _require(len(background_marina) >= 3, "three unrelated marina-cell records are required")
    for persona in ["n1", "n2", "n3", "n3b", "n4", "n5", "n6", "n7", "n8"]:
        phone = _digits(PHONES[persona])
        activity = sum(phone in (row["calling_msisdn"], row["called_msisdn"]) for row in cdr)
        _require(activity >= 3, f"background communications profile too sparse for {persona}")

    by_txn = {row["txn_id"]: row for row in transactions}
    c14 = next(row for row in cdr if row["record_id"] == "c14")
    delta = _parse_timestamp(by_txn["t_88"]["booking_ts_utc"]) - _parse_timestamp(c14["ts_local"])
    _require(delta.total_seconds() == (25 * 60 + 10) * 60, "c14 must be 25h10m before t_88")
    _require(delta.total_seconds() > 24 * 3600, "c14 must be outside the 24h screen")
    _require(delta.total_seconds() < 48 * 3600, "c14 must be inside the 48h contact query")
