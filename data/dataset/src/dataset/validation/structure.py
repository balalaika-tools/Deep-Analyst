"""Validate structural invariants: record counts, stable ID sets, case
namespace consistency, the versioned policy, and static calendar facts."""

from datetime import datetime
from typing import Any

from dataset.core.constants import POLICY_VERSION
from dataset.core.util import _require


def validate_record_counts(
    cdr: list[dict[str, Any]],
    extraction: list[dict[str, Any]],
    emails: list[dict[str, Any]],
    accounts: list[dict[str, Any]],
    transactions: list[dict[str, Any]],
    documents: list[dict[str, Any]],
) -> None:
    _require(len(cdr) == 55, "CDR total must be 55")
    _require(len(extraction) == 18, "extraction total must be 18")
    _require(len(emails) == 6, "email total must be 6")
    _require(len(transactions) == 35, "transaction total must be 35")
    _require(len(accounts) == 18, "account total must be 18")
    _require(len(documents) == 10, "document total must be 10")
    _require(
        sum(map(len, [cdr, extraction, emails, transactions, accounts, documents])) == 142,
        "all-source total must be 142",
    )


def validate_stable_ids_and_case_namespace(
    case_id: str,
    cdr: list[dict[str, Any]],
    extraction: list[dict[str, Any]],
    emails: list[dict[str, Any]],
    accounts: list[dict[str, Any]],
    transactions: list[dict[str, Any]],
    documents: list[dict[str, Any]],
) -> set[str]:
    expected_cdr_ids = {f"c{index:02d}" for index in range(1, 56)}
    expected_extraction_ids = {
        "X-204",
        "X-205",
        "X-206",
        "X-207",
        "X-208",
        "X-301",
        "X-302",
        "X-303",
    } | {f"X-N{index:02d}" for index in range(1, 11)}
    expected_email_ids = {f"eM{index}" for index in range(1, 7)}
    expected_account_ids = {
        "acct_pa",
        "acct_pr",
        "acct_pd",
        "acct_aegean",
        "acct_meridian",
        "acct_ionian",
    } | {f"nA{index:02d}" for index in range(1, 13)}
    expected_transaction_ids = {
        "t_60",
        "t_85",
        "t_86",
        "t_88",
        "t_90",
        "t_B1",
        "t_B2",
        "t_B3",
    } | {f"nT{index:02d}" for index in range(1, 28)}
    expected_document_ids = {
        "R-01",
        "R-02",
        "R-03",
        "R-04",
        "R-05",
        "R-06",
        "A-D1",
        "N-D1",
        "N-D2",
        "N-D3",
    }

    _require({row["record_id"] for row in cdr} == expected_cdr_ids, "CDR stable IDs differ")
    _require(
        {row["msg_id"] for row in extraction} == expected_extraction_ids,
        "extraction stable IDs differ",
    )
    _require({row["email_id"] for row in emails} == expected_email_ids, "email stable IDs differ")
    _require(
        {row["account_id"] for row in accounts} == expected_account_ids, "account stable IDs differ"
    )
    _require(
        {row["txn_id"] for row in transactions} == expected_transaction_ids,
        "transaction stable IDs differ",
    )
    _require(
        {row["document_id"] for row in documents} == expected_document_ids,
        "document stable IDs differ",
    )

    for collection in [cdr, extraction, accounts, transactions]:
        _require(all(row["case_id"] == case_id for row in collection), "case namespace mismatch")
    _require(all(row["headers"]["X-Case-ID"] == case_id for row in emails), "email case mismatch")
    _require(
        all(row["front_matter"]["case_id"] == case_id for row in documents),
        "document case mismatch",
    )

    return (
        expected_cdr_ids
        | expected_extraction_ids
        | expected_email_ids
        | expected_account_ids
        | expected_transaction_ids
        | expected_document_ids
    )


def validate_policy(policy: dict[str, Any]) -> None:
    _require(policy["policy_version"] == POLICY_VERSION, "policy version mismatch")
    _require(
        policy["reconciliation"]["timestamp_tolerance_seconds"] == 90,
        "reconciliation tolerance differs",
    )
    _require(
        policy["screening"]["comms_before_transfer"]["lookback_hours"] == 24,
        "screen lookback differs",
    )


def validate_calendar_facts() -> None:
    # Calendar facts used by business-day screening and the story timeline.
    expected_weekdays = {
        "2026-02-26": 3,
        "2026-03-02": 0,
        "2026-03-03": 1,
        "2026-03-04": 2,
        "2026-03-05": 3,
        "2026-03-06": 4,
    }
    _require(
        all(
            datetime.fromisoformat(day).weekday() == weekday
            for day, weekday in expected_weekdays.items()
        ),
        "verified calendar fact differs",
    )
