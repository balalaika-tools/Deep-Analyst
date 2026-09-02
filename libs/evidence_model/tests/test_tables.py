import evidence_model  # noqa: F401  (registers the tables on SQLModel.metadata)
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlmodel import SQLModel

EXPECTED_TABLES = {
    "records",
    "entities",
    "relationships",
    "transactions",
    "accounts",
    "communications",
    "chunks",
    "ingestion_runs",
}


def _ddl() -> dict[str, str]:
    dialect = PGDialect_psycopg()  # type: ignore[no-untyped-call]
    statements: dict[str, str] = {}
    for table in SQLModel.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=dialect))
        ddl += "".join(str(CreateIndex(index).compile(dialect=dialect)) for index in table.indexes)
        statements[table.name] = ddl
    return statements


def test_every_table_compiles_for_postgresql() -> None:
    ddl = _ddl()
    assert set(ddl) == EXPECTED_TABLES


def test_natural_key_constraints_and_projection_indexes_are_declared() -> None:
    ddl = _ddl()
    assert "uq_records_natural_key" in ddl["records"]
    assert "uq_entities_key" in ddl["entities"]
    assert "uq_chunks_span" in ddl["chunks"]
    assert "ix_transactions_amount" in ddl["transactions"]
    assert "ix_transactions_booking" in ddl["transactions"]
    assert "ix_communications_from" in ddl["communications"]
    assert "ix_accounts_iban" in ddl["accounts"]
    assert "_".join(("case", "id")) not in "".join(ddl.values()).lower()


def test_chunk_embedding_is_a_dimensionless_vector_and_payloads_are_jsonb() -> None:
    ddl = _ddl()
    assert "embedding VECTOR" in ddl["chunks"]
    assert "payload JSONB NOT NULL" in ddl["records"]
    assert (
        "FOREIGN KEY(record_id) REFERENCES records (record_id) ON DELETE CASCADE" in ddl["chunks"]
    )
