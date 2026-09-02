from pathlib import Path

import pytest
from ingestion.adapters.fixtures.bank import STAGING_SCHEMA, load_bank, strip_transaction_control
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.mark.asyncio
async def test_bank_sql_loads_18_accounts_and_35_transactions_and_drops_the_staging_schema(
    engine: AsyncEngine, edition_dir: Path
) -> None:
    async with engine.begin() as conn:
        batch = await load_bank(conn, edition_dir)
        schemas = await conn.execute(
            text("SELECT count(*) FROM pg_namespace WHERE nspname = :name"),
            {"name": STAGING_SCHEMA},
        )

    assert len(batch.accounts) == 18 and len(batch.transactions) == 35
    assert len(batch.records) == 53
    assert schemas.scalar_one() == 0
    assert [a.account_id for a in batch.accounts[:3]] == ["acct_pa", "acct_pr", "acct_pd"]

    t88 = next(t for t in batch.transactions if t.txn_id == "t_88")
    record = next(r for r in batch.records if r.source_record_id == "t_88")
    assert t88.amount_minor == 980000 and t88.currency == "EUR"
    assert record.payload["normalized"]["amount_minor"] == 980000
    assert record.payload["amount_text"] == "9800.00"
    assert record.payload["normalized"]["invoice_refs"] == ["INV-2231"]
    assert record.payload["booking_ts_utc"] == "2026-03-05T14:30:00Z"
    assert record.payload["normalized"]["booking_ts_utc"] == "2026-03-05T14:30:00+00:00"
    assert record.text == "consulting services INV-2231"
    assert record.original_time == "2026-03-05T14:30:00Z"
    acct = next(a for a in batch.accounts if a.account_id == "acct_meridian")
    assert acct.holder_type == "organization" and acct.iban.endswith("4401")


def test_transaction_control_is_stripped_and_nothing_else() -> None:
    script = "BEGIN;\nCREATE TABLE t (x TEXT);\nINSERT INTO t VALUES ('BEGIN;');\nCOMMIT;\n"
    assert (
        strip_transaction_control(script)
        == "CREATE TABLE t (x TEXT);\nINSERT INTO t VALUES ('BEGIN;');"
    )
