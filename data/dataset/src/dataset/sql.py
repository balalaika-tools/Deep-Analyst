"""Render generated accounts and transactions into PostgreSQL-compatible SQL."""

from collections.abc import Sequence
from typing import Any

from dataset.core.constants import ACCOUNT_COLUMNS, TRANSACTION_COLUMNS


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def _render_insert(table: str, columns: Sequence[str], rows: list[dict[str, Any]]) -> str:
    lines = ["INSERT INTO {} ({}) VALUES".format(table, ", ".join(columns))]
    for index, row in enumerate(rows):
        suffix = ";" if index == len(rows) - 1 else ","
        values = ", ".join(_sql_literal(row[column]) for column in columns)
        lines.append(f"    ({values}){suffix}")
    return "\n".join(lines)


def render_bank_sql(accounts: list[dict[str, Any]], transactions: list[dict[str, Any]]) -> str:
    parts = [
        """-- TRG synthetic bank fixture (PostgreSQL 14+)
-- Synthetic data only. No identifier or event belongs to a real subject.

BEGIN;

CREATE TABLE accounts (
    account_id     TEXT NOT NULL,
    iban           TEXT NOT NULL,
    holder_name    TEXT,
    holder_type    TEXT CHECK (holder_type IN ('person', 'organization')),
    bic            TEXT,
    opened_date    TEXT,
    source_version TEXT NOT NULL,
    PRIMARY KEY (account_id),
    UNIQUE (iban)
);

CREATE TABLE transactions (
    txn_id          TEXT NOT NULL,
    booking_ts_utc  TEXT NOT NULL,
    value_date      TEXT NOT NULL,
    debtor_name     TEXT,
    debtor_iban     TEXT NOT NULL,
    debtor_bic      TEXT,
    creditor_name   TEXT,
    creditor_iban   TEXT NOT NULL,
    creditor_bic    TEXT,
    amount_text     TEXT NOT NULL,
    currency        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'booked',
    remittance_info TEXT,
    source_version  TEXT NOT NULL,
    PRIMARY KEY (txn_id),
    FOREIGN KEY (debtor_iban) REFERENCES accounts (iban),
    FOREIGN KEY (creditor_iban) REFERENCES accounts (iban)
);""",
        _render_insert("accounts", ACCOUNT_COLUMNS, accounts),
        _render_insert("transactions", TRANSACTION_COLUMNS, transactions),
        "COMMIT;",
    ]
    return "\n\n".join(parts) + "\n"
