"""SQLModel tables of the evidence store.

`records`, `entities`, and `relationships` are the source of truth. `transactions`,
`accounts`, `communications`, and `chunks` are typed projections rebuilt from records
on every ingestion run; `ingestion_runs` is the run ledger. Index types that SQLModel
cannot express (BM25, HNSW) and the embedding dimension are applied by the owning
service after `create_all`.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def _utc_column(*, nullable: bool = True, index: bool = False) -> Column[datetime]:
    return Column(DateTime(timezone=True), nullable=nullable, index=index)


def _jsonb_column(*, nullable: bool = False) -> Column[Any]:
    return Column(JSONB, nullable=nullable)


class RecordRow(SQLModel, table=True):
    __tablename__ = "records"
    __table_args__ = (
        UniqueConstraint(
            "case_id", "source_system", "source_record_id", name="uq_records_natural_key"
        ),
        Index("ix_records_case_record_type", "case_id", "record_type"),
    )

    record_id: str = Field(primary_key=True)
    case_id: str
    source_system: str
    source_record_id: str
    record_type: str
    event_time_utc: datetime | None = Field(default=None, sa_column=_utc_column())
    original_time: str | None = None
    text: str | None = None
    payload: dict[str, Any] = Field(sa_column=_jsonb_column())
    source_path: str
    content_hash: str


class EntityRow(SQLModel, table=True):
    __tablename__ = "entities"
    __table_args__ = (
        UniqueConstraint("case_id", "entity_type", "normalized_key", name="uq_entities_key"),
        Index("ix_entities_case_type", "case_id", "entity_type"),
    )

    entity_id: str = Field(primary_key=True)
    case_id: str
    entity_type: str
    label: str
    normalized_key: str | None = None
    source_refs: list[dict[str, Any]] = Field(sa_column=_jsonb_column())


class RelationshipRow(SQLModel, table=True):
    __tablename__ = "relationships"
    __table_args__ = (
        Index("ix_relationships_case_subject", "case_id", "subject_entity_id"),
        Index("ix_relationships_case_object", "case_id", "object_entity_id"),
        Index("ix_relationships_case_predicate", "case_id", "predicate"),
    )

    relationship_id: str = Field(primary_key=True)
    case_id: str
    subject_entity_id: str = Field(
        sa_column=Column(ForeignKey("entities.entity_id"), nullable=False)
    )
    predicate: str
    object_entity_id: str = Field(
        sa_column=Column(ForeignKey("entities.entity_id"), nullable=False)
    )
    status: str
    method: str
    occurred_at: datetime | None = Field(default=None, sa_column=_utc_column())
    valid_from: datetime | None = Field(default=None, sa_column=_utc_column())
    valid_to: datetime | None = Field(default=None, sa_column=_utc_column())
    source_refs: list[dict[str, Any]] = Field(sa_column=_jsonb_column())
    attributes: dict[str, Any] = Field(sa_column=_jsonb_column())


class TransactionRow(SQLModel, table=True):
    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_case_booking", "case_id", "booking_ts_utc"),
        Index("ix_transactions_case_amount", "case_id", "amount_minor"),
        Index("ix_transactions_case_debtor", "case_id", "debtor_iban"),
        Index("ix_transactions_case_creditor", "case_id", "creditor_iban"),
    )

    record_id: str = Field(
        sa_column=Column(ForeignKey("records.record_id", ondelete="CASCADE"), primary_key=True)
    )
    case_id: str
    txn_id: str
    booking_ts_utc: datetime = Field(sa_column=_utc_column(nullable=False))
    value_date: date
    debtor_iban: str
    debtor_name: str | None = None
    creditor_iban: str
    creditor_name: str | None = None
    amount_minor: int
    amount_text: str
    currency: str
    status: str
    remittance_info: str | None = None


class AccountRow(SQLModel, table=True):
    __tablename__ = "accounts"
    __table_args__ = (Index("ix_accounts_case_iban", "case_id", "iban"),)

    record_id: str = Field(
        sa_column=Column(ForeignKey("records.record_id", ondelete="CASCADE"), primary_key=True)
    )
    case_id: str
    account_id: str
    iban: str
    holder_name: str | None = None
    holder_type: str | None = None
    bic: str | None = None
    opened_date: date | None = None


class CommunicationRow(SQLModel, table=True):
    __tablename__ = "communications"
    __table_args__ = (
        Index("ix_communications_case_time", "case_id", "event_time_utc"),
        Index("ix_communications_case_from", "case_id", "from_endpoint"),
        Index("ix_communications_case_to", "case_id", "to_endpoint"),
    )

    record_id: str = Field(
        sa_column=Column(ForeignKey("records.record_id", ondelete="CASCADE"), primary_key=True)
    )
    case_id: str
    channel: str
    direction: str
    from_endpoint: str
    to_endpoint: str
    event_time_utc: datetime = Field(sa_column=_utc_column(nullable=False))
    original_time: str
    duration_s: int | None = None
    device_id: str | None = None


class ChunkRow(SQLModel, table=True):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("record_id", "char_start", "char_end", name="uq_chunks_span"),
        Index("ix_chunks_case_source", "case_id", "source_system"),
        Index("ix_chunks_case_time", "case_id", "event_time_utc"),
    )

    chunk_id: str = Field(primary_key=True)
    record_id: str = Field(
        sa_column=Column(ForeignKey("records.record_id", ondelete="CASCADE"), nullable=False)
    )
    case_id: str
    char_start: int
    char_end: int
    text: str
    source_system: str
    event_time_utc: datetime | None = Field(default=None, sa_column=_utc_column())
    # Declared without a dimension; the owning service sets `vector(<n>)` from its
    # configured embedding size before building the HNSW index.
    embedding: list[float] | None = Field(default=None, sa_column=Column(Vector()))


class IngestionRunRow(SQLModel, table=True):
    __tablename__ = "ingestion_runs"
    __table_args__ = (Index("ix_ingestion_runs_fingerprint", "case_id", "fingerprint"),)

    run_id: str = Field(primary_key=True)
    case_id: str
    fingerprint: str
    dataset_version: str
    embedding_model_id: str
    started_at: datetime = Field(sa_column=_utc_column(nullable=False))
    completed_at: datetime | None = Field(default=None, sa_column=_utc_column())
    outcome: str
    summary: dict[str, Any] = Field(sa_column=_jsonb_column())


PROJECTION_TABLES: tuple[type[SQLModel], ...] = (
    TransactionRow,
    AccountRow,
    CommunicationRow,
    ChunkRow,
)
