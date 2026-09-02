"""Validate bank-source invariants: IBAN checksums, transaction/account
consistency, amount scale, and the pinned amount-band controls."""

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from dataset.core.util import _require, _valid_iban


def validate_accounts_and_transactions(
    accounts: list[dict[str, Any]], transactions: list[dict[str, Any]]
) -> None:
    _require(all(_valid_iban(row["iban"]) for row in accounts), "in-corpus IBAN checksum failure")

    account_ibans = {row["iban"] for row in accounts}
    _require(len(account_ibans) == 18, "IBANs must be unique")
    for txn in transactions:
        _require(txn["debtor_iban"] in account_ibans, "transaction debtor account missing")
        _require(txn["creditor_iban"] in account_ibans, "transaction creditor account missing")
        _require(
            re.fullmatch(r"[0-9]+\.[0-9]{2}", txn["amount_text"]) is not None,
            "invalid amount_text scale",
        )
        try:
            amount = Decimal(txn["amount_text"])
        except InvalidOperation as exc:
            raise ValueError("invalid decimal amount") from exc
        _require(amount.as_tuple().exponent == -2, "amount_text must retain two decimal places")

    by_txn = {row["txn_id"]: row for row in transactions}
    expected_minor = {
        "t_60": 940000,
        "t_85": 950000,
        "t_86": 970000,
        "t_88": 980000,
        "t_90": 250000,
        "t_B1": 920000,
        "t_B2": 145000,
        "t_B3": 20000,
        "nT01": 160000,
        "nT02": 75000,
        "nT03": 2450,
    }
    for txn_id, minor in expected_minor.items():
        actual = int(Decimal(by_txn[txn_id]["amount_text"]) * 100)
        _require(actual == minor, f"minor-unit mismatch for {txn_id}")

    by_account_id = {row["account_id"]: row for row in accounts}
    aegean_iban = by_account_id["acct_aegean"]["iban"]
    _require(
        not any(row["creditor_iban"] == aegean_iban for row in transactions),
        "acct_aegean must receive no in-window credit",
    )
    background_band = [
        row
        for row in transactions
        if row["txn_id"].startswith("nT")
        and Decimal("9000.00") <= Decimal(row["amount_text"]) <= Decimal("9999.00")
    ]
    _require(
        len(background_band) >= 2, "at least two background transactions must be in the EUR 9k band"
    )
    _require(
        {row["txn_id"] for row in background_band} >= {"nT04", "nT05"},
        "pinned amount-band controls missing",
    )
    _require(
        "INV-2231"
        not in " ".join(
            row["remittance_info"] for row in transactions if row["txn_id"].startswith("nT")
        ),
        "background must not use exact INV-2231",
    )
