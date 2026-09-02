"""Build quarantine fixtures: records designed to fail ingestion in a specific way."""

import csv
import io

from dataset.core import state
from dataset.core.constants import CDR_COLUMNS
from dataset.core.fixtures import DEVICES, PHONES
from dataset.core.state import _tr
from dataset.core.util import _digits, _json_bytes, _make_gr_iban


def build_quarantine() -> tuple[dict[str, bytes], list[dict[str, str]]]:
    cdr_stream = io.StringIO(newline="")
    writer = csv.DictWriter(cdr_stream, fieldnames=CDR_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerow(
        {
            "record_id": "Q-CDR-01",
            "seq": 99901,
            "record_type": "MOC",
            "subscriber_msisdn": _digits(PHONES["n1"]),
            "calling_msisdn": _digits(PHONES["n1"]),
            "called_msisdn": _digits(PHONES["n2"]),
            "imei": DEVICES["n1"],
            "cell_id": "AT-TEST-0001",
            "ts_local": "2026-03-04T25:61:00+XX:YY",
            "duration_s": 30,
            "sms_len": "",
            "source_version": state.SOURCE_VERSIONS["cdr"],
        }
    )

    invalid_iban = {
        "account_id": "Q-BANK-01",
        "iban": "GR0000000000000000000000000",
        "holder_name": "Invalid Fixture",
        "holder_type": "person",
        "bic": "TRGSGR2A",
        "opened_date": "2026-02-20",
        "source_version": state.SOURCE_VERSIONS["bank"],
    }
    invalid_scale = {
        "txn_id": "Q-BANK-02",
        "booking_ts_utc": "2026-03-04T10:00:00Z",
        "value_date": "2026-03-04",
        "debtor_name": "Fixture Debtor",
        "debtor_iban": _make_gr_iban(1, "7719"),
        "debtor_bic": "TRGSGR2A",
        "creditor_name": "Fixture Creditor",
        "creditor_iban": _make_gr_iban(5, "4401"),
        "creditor_bic": "TRGSGR2A",
        "amount_text": "10.001",
        "currency": "EUR",
        "status": "booked",
        "remittance_info": "currency-scale fixture",
        "source_version": state.SOURCE_VERSIONS["bank"],
    }
    duplicate_a = {
        "msg_id": "Q-DUP-01",
        "imei": DEVICES["n1"],
        "subscriber_msisdn": PHONES["n1"],
        "direction": "out",
        "peer": PHONES["n2"],
        "app": "sms",
        "ts_utc": "2026-03-04T10:00:00Z",
        "body": _tr("πρώτη έκδοση", "first version"),
        "source_version": state.SOURCE_VERSIONS["extraction"],
    }
    duplicate_b = {
        "msg_id": "Q-DUP-01",
        "imei": DEVICES["n1"],
        "subscriber_msisdn": PHONES["n1"],
        "direction": "out",
        "peer": PHONES["n2"],
        "app": "sms",
        "ts_utc": "2026-03-04T10:00:00Z",
        "body": _tr("διαφορετικό περιεχόμενο με ίδιο ID", "different content with the same ID"),
        "source_version": state.SOURCE_VERSIONS["extraction"],
    }
    malformed_email = (
        b"From: missing-required-fields@invalid.example\n"
        b"Subject: malformed fixture\n"
        b"Content-Type: text/plain; charset=UTF-8\n\n"
        b"This fixture deliberately omits Message-ID, To, Date and synthetic metadata.\n"
    )

    files = {
        "Q-CDR-01.csv": cdr_stream.getvalue().encode("utf-8"),
        "Q-BANK-01.json": _json_bytes(invalid_iban, pretty=True),
        "Q-BANK-02.json": _json_bytes(invalid_scale, pretty=True),
        "Q-DUP-01.jsonl": _json_bytes(duplicate_a) + _json_bytes(duplicate_b),
        "Q-EML-01.eml": malformed_email,
        "Q-DOC-01.bin": b"\x00TRG-UNSUPPORTED-BINARY\xff\x10",
    }
    expected = [
        {
            "fixture_id": "Q-CDR-01",
            "file": "Q-CDR-01.csv",
            "expected_outcome": "timestamp_parse_error",
        },
        {
            "fixture_id": "Q-BANK-01",
            "file": "Q-BANK-01.json",
            "expected_outcome": "iban_checksum_error",
        },
        {
            "fixture_id": "Q-BANK-02",
            "file": "Q-BANK-02.json",
            "expected_outcome": "currency_scale_error",
        },
        {
            "fixture_id": "Q-DUP-01",
            "file": "Q-DUP-01.jsonl",
            "expected_outcome": "conflict_no_overwrite",
        },
        {
            "fixture_id": "Q-EML-01",
            "file": "Q-EML-01.eml",
            "expected_outcome": "email_header_error",
        },
        {
            "fixture_id": "Q-DOC-01",
            "file": "Q-DOC-01.bin",
            "expected_outcome": "unsupported_format",
        },
    ]
    return files, expected
