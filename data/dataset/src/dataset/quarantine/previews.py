"""Build canonical/raw preview snippets: one worked example per source feed."""

from typing import Any

from dataset.core import state
from dataset.core.constants import ACCOUNT_COLUMNS, GENERATED_AT, TRANSACTION_COLUMNS
from dataset.core.fixtures import DEVICES, PHONES
from dataset.core.state import _tr
from dataset.core.util import _cdr_lexemes, _global_record_id, _ordered_row_hash, _record_hash


def _canonical_envelope(
    source: str,
    source_record_id: str,
    raw_path: str,
    raw_content_hash: str,
    record_type: str,
    event_time_utc: str | None,
    original_time_value: str | None,
    normalized_payload: dict[str, Any],
) -> dict[str, Any]:
    record_id = _global_record_id(source, source_record_id)
    return {
        "record_id": record_id,
        "record_version_id": f"{record_id}:{raw_content_hash}",
        "source_system": source,
        "source_record_id": source_record_id,
        "source_version_id": state.SOURCE_VERSIONS[source],
        "raw_object_uri": raw_path,
        "raw_content_hash": raw_content_hash,
        "record_type": record_type,
        "event_time_utc": event_time_utc,
        "original_time_value": original_time_value,
        "normalized_payload": normalized_payload,
        "parser_version": f"{source}-parser@1",
        "ingested_at": GENERATED_AT,
    }


def build_previews(
    cdr: list[dict[str, Any]],
    extraction: list[dict[str, Any]],
    emails: list[dict[str, Any]],
    accounts: list[dict[str, Any]],
    transactions: list[dict[str, Any]],
    documents: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    c01 = next(row for row in cdr if row["record_id"] == "c01")
    x204 = next(row for row in extraction if row["msg_id"] == "X-204")
    em1 = next(row for row in emails if row["email_id"] == "eM1")
    account = next(row for row in accounts if row["account_id"] == "acct_aegean")
    txn = next(row for row in transactions if row["txn_id"] == "t_88")
    document = next(row for row in documents if row["document_id"] == "R-03")

    email_raw = {"email_id": em1["email_id"], "headers": em1["headers"], "body": em1["body"]}
    document_raw = {
        "document_id": document["document_id"],
        "front_matter": document["front_matter"],
        "body": document["body"],
    }
    previews = {
        "cdr": {
            "raw": c01,
            "canonical": _canonical_envelope(
                "cdr",
                "c01",
                "raw/cdr.csv",
                _record_hash(_cdr_lexemes(c01)),
                "SMS",
                "2026-03-04T21:14:00Z",
                c01["ts_local"],
                {
                    "sender": PHONES["pa"],
                    "recipient": PHONES["pr"],
                    "channel": "sms",
                    "imei": DEVICES["pa"],
                },
            ),
        },
        "extraction": {
            "raw": x204,
            "canonical": _canonical_envelope(
                "extraction",
                "X-204",
                "raw/extraction.jsonl",
                _record_hash(x204),
                "MESSAGE",
                x204["ts_utc"],
                x204["ts_utc"],
                {
                    "sender": PHONES["pa"],
                    "recipient": PHONES["pr"],
                    "channel": "sms",
                    "body": x204["body"],
                },
            ),
        },
        "email": {
            "raw": email_raw,
            "canonical": _canonical_envelope(
                "email",
                "eM1",
                "raw/emails/eM1.eml",
                _record_hash(email_raw),
                "EMAIL",
                "2026-03-04T16:40:11Z",
                em1["headers"]["Date"],
                {
                    "from": "alex@meridian-consulting.example",
                    "to": ["k.rossi@aegeantrade.example"],
                    "subject": _tr("Πέμπτη", "Thursday"),
                },
            ),
        },
        "account": {
            "raw": account,
            "canonical": _canonical_envelope(
                "bank",
                "acct_aegean",
                "raw/bank.sql",
                _ordered_row_hash(account, ACCOUNT_COLUMNS),
                "ACCOUNT",
                None,
                account["opened_date"],
                {
                    "account_id": account["account_id"],
                    "iban": account["iban"],
                    "holder_name": account["holder_name"],
                },
            ),
        },
        "transaction": {
            "raw": txn,
            "canonical": _canonical_envelope(
                "bank",
                "t_88",
                "raw/bank.sql",
                _ordered_row_hash(txn, TRANSACTION_COLUMNS),
                "TRANSACTION",
                txn["booking_ts_utc"],
                txn["booking_ts_utc"],
                {
                    "debtor_account_entity_id": "ent_account_aegean",
                    "creditor_account_entity_id": "ent_account_meridian",
                    "amount_minor": 980000,
                    "currency": "EUR",
                    "original_amount_text": "9800.00",
                },
            ),
        },
        "document": {
            "raw": document_raw,
            "canonical": _canonical_envelope(
                "docs",
                "R-03",
                "raw/docs/R-03.md",
                _record_hash(document_raw),
                "DOCUMENT",
                "2026-03-09T00:00:00Z",
                document["front_matter"]["document_date"],
                {"genre": "sar_narrative", "source_reliability": "high"},
            ),
        },
    }
    return previews
