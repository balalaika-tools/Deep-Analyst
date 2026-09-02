"""bank.sql through a transient staging schema; PostgreSQL does the parsing."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from ingestion.domain.identifiers import find_identifiers
from ingestion.domain.normalization import money_to_minor_units, normalize_iban, to_utc
from ingestion.domain.records import (
    AccountProjection,
    SourceBatch,
    SourceRecord,
    TransactionProjection,
    content_hash,
)

SOURCE_SYSTEM = "bank"
RELATIVE_PATH = "raw/bank.sql"
STAGING_SCHEMA = "bank_raw"
_ACCOUNT_COLUMNS = (
    "account_id", "iban", "holder_name", "holder_type", "bic", "opened_date",
    "source_version",
)  # fmt: skip
_TRANSACTION_COLUMNS = (
    "txn_id", "booking_ts_utc", "value_date", "debtor_name", "debtor_iban",
    "debtor_bic", "creditor_name", "creditor_iban", "creditor_bic", "amount_text", "currency",
    "status", "remittance_info", "source_version",
)  # fmt: skip
_TRANSACTION_CONTROL = {"BEGIN;", "COMMIT;"}


def strip_transaction_control(script: str) -> str:
    """The loader owns the transaction, so the script's own BEGIN/COMMIT are dropped."""
    return "\n".join(
        line for line in script.splitlines() if line.strip().upper() not in _TRANSACTION_CONTROL
    )


def _account(row: dict[str, Any]) -> tuple[SourceRecord, AccountProjection]:
    iban = normalize_iban(str(row["iban"]))
    payload: dict[str, Any] = {**row, "normalized": {"iban": iban}}
    record = SourceRecord(
        source_system=SOURCE_SYSTEM,
        source_record_id=str(row["account_id"]),
        record_type="account",
        event_time_utc=None,
        original_time=None,
        text=None,
        payload=payload,
        source_path=RELATIVE_PATH,
        content_hash=content_hash(row),
    )
    opened = row.get("opened_date")
    projection = AccountProjection(
        record_id=record.record_id,
        account_id=str(row["account_id"]),
        iban=iban,
        holder_name=row.get("holder_name"),
        holder_type=row.get("holder_type"),
        bic=row.get("bic"),
        opened_date=date.fromisoformat(str(opened)) if opened else None,
    )
    return record, projection


def _transaction(row: dict[str, Any]) -> tuple[SourceRecord, TransactionProjection]:
    booking = to_utc(str(row["booking_ts_utc"]))
    amount_minor = money_to_minor_units(str(row["amount_text"]), str(row["currency"]))
    remittance = row.get("remittance_info")
    references = [span.normalized_key for span in find_identifiers(remittance or "")]
    debtor_iban = normalize_iban(str(row["debtor_iban"]))
    creditor_iban = normalize_iban(str(row["creditor_iban"]))
    payload: dict[str, Any] = {
        **row,
        "normalized": {
            "amount_minor": amount_minor,
            "currency": str(row["currency"]).upper(),
            "booking_ts_utc": booking.isoformat(),
            "debtor_iban": debtor_iban,
            "creditor_iban": creditor_iban,
            "invoice_refs": references,
        },
    }
    record = SourceRecord(
        source_system=SOURCE_SYSTEM,
        source_record_id=str(row["txn_id"]),
        record_type="transaction",
        event_time_utc=booking,
        original_time=str(row["booking_ts_utc"]),
        text=str(remittance) if remittance else None,
        payload=payload,
        source_path=RELATIVE_PATH,
        content_hash=content_hash(row),
    )
    projection = TransactionProjection(
        record_id=record.record_id,
        txn_id=str(row["txn_id"]),
        booking_ts_utc=booking,
        value_date=date.fromisoformat(str(row["value_date"])),
        debtor_iban=debtor_iban,
        debtor_name=row.get("debtor_name"),
        creditor_iban=creditor_iban,
        creditor_name=row.get("creditor_name"),
        amount_minor=amount_minor,
        amount_text=str(row["amount_text"]),
        currency=str(row["currency"]).upper(),
        status=str(row["status"]),
        remittance_info=remittance,
    )
    return record, projection


async def _rows(
    conn: AsyncConnection, table: str, columns: tuple[str, ...]
) -> list[dict[str, Any]]:
    # ctid order is insertion order for a heap table filled in this transaction, so
    # records keep the file order the manifest lists them in.
    result = await conn.execute(
        text(f"SELECT {', '.join(columns)} FROM {STAGING_SCHEMA}.{table} ORDER BY ctid")
    )
    return [dict(zip(columns, row, strict=True)) for row in result.all()]


async def load_bank(conn: AsyncConnection, edition_dir: Path) -> SourceBatch:
    """Execute the fixture SQL in `bank_raw`, read it back, then drop the schema."""
    script = strip_transaction_control((edition_dir / RELATIVE_PATH).read_text(encoding="utf-8"))
    await conn.execute(text(f"DROP SCHEMA IF EXISTS {STAGING_SCHEMA} CASCADE"))
    await conn.execute(text(f"CREATE SCHEMA {STAGING_SCHEMA}"))
    await conn.execute(text(f"SET LOCAL search_path TO {STAGING_SCHEMA}, public"))
    try:
        await conn.exec_driver_sql(script)
        accounts = [_account(row) for row in await _rows(conn, "accounts", _ACCOUNT_COLUMNS)]
        transactions = [
            _transaction(row) for row in await _rows(conn, "transactions", _TRANSACTION_COLUMNS)
        ]
    finally:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {STAGING_SCHEMA} CASCADE"))
    return SourceBatch(
        source_system=SOURCE_SYSTEM,
        records=[record for record, _ in accounts] + [record for record, _ in transactions],
        accounts=[projection for _, projection in accounts],
        transactions=[projection for _, projection in transactions],
    )
