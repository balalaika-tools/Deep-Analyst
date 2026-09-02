"""Locate the exact fields and text spans that ground-truth assertions cite."""

from collections.abc import Sequence
from typing import Any

from dataset.core.state import _tr
from dataset.core.util import _require


def _nested_value(record: dict[str, Any], field_path: str) -> Any:
    value: Any = record
    for part in field_path.split("."):
        value = value[part]
    return value


def _text_span_locator(
    logical_record: dict[str, Any], field_path: str, raw_value: str
) -> dict[str, Any]:
    text = str(_nested_value(logical_record, field_path))
    start = text.find(raw_value)
    _require(start >= 0, f"source span not found: {raw_value} in {field_path}")
    return {
        "kind": "text_span",
        "field_path": field_path,
        "char_start": start,
        "char_end": start + len(raw_value),
        "raw_value": raw_value,
    }


def _field_locator(logical_record: dict[str, Any], field_path: str) -> dict[str, Any]:
    return {
        "kind": "field",
        "field_path": field_path,
        "raw_value": _nested_value(logical_record, field_path),
    }


def _field_set_locator(
    logical_record: dict[str, Any], field_paths: Sequence[str]
) -> dict[str, Any]:
    return {
        "kind": "field_set",
        "fields": [_field_locator(logical_record, path) for path in field_paths],
    }


def _assertion_locator(
    record_id: str,
    subject: str,
    predicate: str,
    obj: str,
    catalog_entry: dict[str, Any],
) -> dict[str, Any]:
    exact_spans = {
        ("R-01", "ent_person_mavridis_a", "USES"): (
            "body",
            _tr("Χρησιμοποιεί το τηλέφωνο +30 697 123 4567", "He uses telephone +30 697 123 4567"),
        ),
        ("R-01", "ent_person_sofia", "USES"): (
            "body",
            _tr(
                "Στις 21 Φεβρουαρίου, 11:00–11:30 τοπική ώρα, καταγράφηκε χρήση του ίδιου τηλεφώνου από τη Sofia Andreou",
                "On 21 February, from 11:00–11:30 local time, use of the same telephone by Sofia Andreou was recorded",
            ),
        ),
        ("R-01", "ent_person_mavridis_d", "USES"): (
            "body",
            _tr("Τηλέφωνο Dimitris: +30 691 222 3344", "Dimitris telephone: +30 691 222 3344"),
        ),
        ("R-01", "ent_person_mavridis_d", "KIN_OF"): (
            "body",
            _tr(
                "Ο Dimitris Mavridis αναφέρεται ως ξάδελφός του και είναι διαφορετικό πρόσωπο",
                "Dimitris Mavridis is reported to be his cousin and is a different person",
            ),
        ),
        ("R-01", "ent_person_mavridis_a", "ASSOCIATED_WITH"): (
            "body",
            _tr(
                "Υπάρχει πιθανή, όχι επιβεβαιωμένη, σύνδεση με τη Meridian Consulting Ltd",
                "There is a possible, unconfirmed association with Meridian Consulting Ltd",
            ),
        ),
        ("R-02", "ent_person_sofia", "USES"): (
            "body",
            _tr("Διοικητική επαφή: Sofia Andreou", "Administrative contact: Sofia Andreou"),
        ),
        ("R-02", "ent_person_rossi", "USES"): (
            "body",
            _tr(
                "Διευθύντρια: K. Rossi / Κ. Ρόσση. Τηλέφωνο: 694 987 6543",
                "Director: K. Rossi / Katherine Rossi. Telephone: 694 987 6543",
            ),
        ),
        ("R-02", "ent_person_rossi", "DIRECTOR_OF"): (
            "body",
            _tr("Διευθύντρια: K. Rossi / Κ. Ρόσση", "Director: K. Rossi / Katherine Rossi"),
        ),
        ("R-02", "ent_account_meridian", "HELD_BY"): (
            "body",
            _tr("Ο λογαριασμός της Meridian λήγει σε 4401", "Meridian's account ends in 4401"),
        ),
        ("R-03", "ent_org_aegean", "ASSOCIATED_WITH"): (
            "body",
            _tr(
                "Καταχωρισμένο τηλέφωνο επικοινωνίας: +30 210 445 5667",
                "Registered contact telephone: +30 210 445 5667",
            ),
        ),
        ("eM2", "ent_person_rossi", "USES"): ("body", "6949876543"),
        ("eM5", "ent_person_mavridis_d", "EMPLOYED_BY"): (
            "body",
            _tr("εργάζεται στη Logistiki Attikis", "is employed by Logistiki Attikis"),
        ),
        ("eM5", "ent_org_logistiki_b1", "ASSOCIATED_WITH"): (
            "body",
            _tr("Τηλεφωνικό κέντρο: +30 210 111 2233", "Main telephone: +30 210 111 2233"),
        ),
        ("eM6", "ent_person_papadakis_n2", "USES"): (
            "body",
            _tr(
                "Οδηγός: G. Papadakis, τηλέφωνο +30 693 000 0102",
                "Driver: G. Papadakis, telephone +30 693 000 0102",
            ),
        ),
    }
    logical_record = catalog_entry["_logical_record"]
    exact = exact_spans.get((record_id, subject, predicate))
    if exact:
        return _text_span_locator(logical_record, exact[0], exact[1])
    source = catalog_entry["source"]
    if source == "cdr":
        return _field_set_locator(
            logical_record,
            ["calling_msisdn", "called_msisdn", "subscriber_msisdn", "imei", "ts_local"],
        )
    if source == "extraction":
        return _field_set_locator(
            logical_record, ["subscriber_msisdn", "direction", "peer", "imei", "ts_utc", "body"]
        )
    if source == "email":
        return _field_set_locator(
            logical_record,
            ["headers.From", "headers.To", "headers.Date", "headers.Subject", "body"],
        )
    if source == "bank" and record_id.startswith("acct_"):
        return _field_set_locator(
            logical_record, ["account_id", "iban", "holder_name", "holder_type"]
        )
    if source == "bank":
        return _field_set_locator(
            logical_record,
            [
                "booking_ts_utc",
                "debtor_name",
                "debtor_iban",
                "creditor_name",
                "creditor_iban",
                "amount_text",
                "currency",
                "status",
                "remittance_info",
            ],
        )
    return {"kind": "record", "field_path": "$", "hash_scope": "canonical_logical_record"}
