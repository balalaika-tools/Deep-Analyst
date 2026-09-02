"""One-shot, owner-only and idempotent initialization of agent database objects."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection, sql
from psycopg.rows import dict_row

INITIALIZER_SCHEMA_VERSION = "agent-runtime@1"
READER_ROLE = "agent_reader"
WRITER_ROLE = "agent_writer"

REQUIRED_EVIDENCE_RELATIONS = (
    "public.records",
    "public.entities",
    "public.relationships",
    "public.transactions",
    "public.accounts",
    "public.communications",
    "public.chunks",
)
REQUIRED_EVIDENCE_INDEXES = (
    "public.chunks_text_bm25",
    "public.chunks_embedding_hnsw",
)
REQUIRED_EVIDENCE_COLUMNS: dict[str, frozenset[str]] = {
    "records": frozenset(
        {
            "record_id",
            "case_id",
            "source_system",
            "source_record_id",
            "record_type",
            "event_time_utc",
            "text",
            "payload",
            "source_path",
            "content_hash",
        }
    ),
    "chunks": frozenset(
        {
            "chunk_id",
            "record_id",
            "case_id",
            "char_start",
            "char_end",
            "text",
            "source_system",
            "event_time_utc",
            "embedding",
        }
    ),
    "entities": frozenset({"entity_id", "case_id", "entity_type", "label", "source_refs"}),
    "relationships": frozenset(
        {
            "relationship_id",
            "case_id",
            "subject_entity_id",
            "predicate",
            "object_entity_id",
            "status",
            "occurred_at",
            "source_refs",
        }
    ),
    "transactions": frozenset(
        {
            "record_id",
            "case_id",
            "txn_id",
            "booking_ts_utc",
            "value_date",
            "debtor_iban",
            "debtor_name",
            "creditor_iban",
            "creditor_name",
            "amount_minor",
            "amount_text",
            "currency",
            "status",
            "remittance_info",
        }
    ),
    "accounts": frozenset(
        {
            "record_id",
            "case_id",
            "account_id",
            "iban",
            "holder_name",
            "holder_type",
            "bic",
            "opened_date",
        }
    ),
    "communications": frozenset(
        {
            "record_id",
            "case_id",
            "channel",
            "direction",
            "from_endpoint",
            "to_endpoint",
            "event_time_utc",
            "original_time",
            "duration_s",
            "device_id",
        }
    ),
}

VIEW_DEFINITIONS: dict[str, str] = {
    "transactions_v1": """
        CREATE OR REPLACE VIEW agent_read.transactions_v1
        WITH (security_barrier = true, security_invoker = true) AS
        SELECT
            r.record_id,
            t.case_id,
            r.source_system,
            r.source_record_id,
            r.source_path,
            r.content_hash,
            jsonb_build_array(jsonb_build_object(
                'record_id', r.record_id,
                'locator', jsonb_build_object('kind', 'field', 'field', 'payload')
            )) AS source_refs,
            t.txn_id,
            t.booking_ts_utc,
            t.value_date,
            t.debtor_iban,
            t.debtor_name,
            t.creditor_iban,
            t.creditor_name,
            t.amount_minor,
            t.amount_text,
            t.currency,
            t.status,
            t.remittance_info
        FROM public.transactions AS t
        JOIN public.records AS r
          ON r.record_id = t.record_id AND r.case_id = t.case_id
        WHERE t.case_id = current_setting('app.case_id', true)
    """,
    "accounts_v1": """
        CREATE OR REPLACE VIEW agent_read.accounts_v1
        WITH (security_barrier = true, security_invoker = true) AS
        SELECT
            r.record_id,
            a.case_id,
            r.source_system,
            r.source_record_id,
            r.source_path,
            r.content_hash,
            jsonb_build_array(jsonb_build_object(
                'record_id', r.record_id,
                'locator', jsonb_build_object('kind', 'field', 'field', 'payload')
            )) AS source_refs,
            a.account_id,
            a.iban,
            a.holder_name,
            a.holder_type,
            a.bic,
            a.opened_date
        FROM public.accounts AS a
        JOIN public.records AS r
          ON r.record_id = a.record_id AND r.case_id = a.case_id
        WHERE a.case_id = current_setting('app.case_id', true)
    """,
    "communications_v1": """
        CREATE OR REPLACE VIEW agent_read.communications_v1
        WITH (security_barrier = true, security_invoker = true) AS
        SELECT
            r.record_id,
            c.case_id,
            r.source_system,
            r.source_record_id,
            r.source_path,
            r.content_hash,
            jsonb_build_array(jsonb_build_object(
                'record_id', r.record_id,
                'locator', jsonb_build_object('kind', 'field', 'field', 'payload')
            )) AS source_refs,
            c.channel,
            c.direction,
            c.from_endpoint,
            c.to_endpoint,
            c.event_time_utc,
            c.original_time,
            c.duration_s,
            c.device_id
        FROM public.communications AS c
        JOIN public.records AS r
          ON r.record_id = c.record_id AND r.case_id = c.case_id
        WHERE c.case_id = current_setting('app.case_id', true)
    """,
}


@dataclass(frozen=True, slots=True)
class InitializationResult:
    version: str
    changed: bool


class EvidenceSchemaMissing(RuntimeError):
    pass


class IncompatibleInitializerVersion(RuntimeError):
    pass


async def initialize_database(
    *,
    owner_dsn: str,
    reader_password: str,
    writer_password: str,
    expected_version: str = INITIALIZER_SCHEMA_VERSION,
) -> InitializationResult:
    """Create the agent surface once; the version row is committed only after grants."""

    native_dsn = _native_dsn(owner_dsn)
    connection = await AsyncConnection.connect(native_dsn, row_factory=dict_row)
    try:
        await _verify_evidence_schema(connection)
        recorded = await _recorded_version(connection)
        if recorded == expected_version:
            return InitializationResult(version=expected_version, changed=False)
        if recorded is not None:
            raise IncompatibleInitializerVersion(
                "database has an unsupported agent initializer version"
            )
        async with connection.transaction():
            await _create_roles_and_schemas(
                connection,
                reader_password=reader_password,
                writer_password=writer_password,
            )
            await _create_views_and_version_table(connection)
    finally:
        await connection.close()

    await _setup_checkpointer(native_dsn)

    connection = await AsyncConnection.connect(native_dsn, row_factory=dict_row)
    try:
        async with connection.transaction():
            await _apply_least_privilege_grants(connection)
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "INSERT INTO agent_runtime.schema_version (singleton, version) "
                    "VALUES (true, %s)",
                    (expected_version,),
                )
    finally:
        await connection.close()
    return InitializationResult(version=expected_version, changed=True)


async def _verify_evidence_schema(connection: AsyncConnection[dict[str, Any]]) -> None:
    async with connection.transaction():
        async with connection.cursor() as cursor:
            await cursor.execute(
                "SELECT required.object_name, objects.oid AS object_id, objects.relkind "
                "FROM unnest(%s::text[]) AS required(object_name) "
                "LEFT JOIN pg_class AS objects ON objects.oid = to_regclass(required.object_name)",
                (list((*REQUIRED_EVIDENCE_RELATIONS, *REQUIRED_EVIDENCE_INDEXES)),),
            )
            rows = await cursor.fetchall()
            missing = sorted(
                str(row["object_name"])
                for row in rows
                if (
                    not isinstance(row, Mapping)
                    or row.get("object_id") is None
                    or row.get("relkind")
                    != ("i" if row.get("object_name") in REQUIRED_EVIDENCE_INDEXES else "r")
                )
            )
            if missing:
                raise EvidenceSchemaMissing("required evidence objects are unavailable")
            await cursor.execute(
                "SELECT extname FROM pg_extension WHERE extname = ANY(%s::text[])",
                (["pg_search", "vector"],),
            )
            extensions = {str(row["extname"]) for row in await cursor.fetchall()}
            await cursor.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = ANY(%s::text[])",
                (sorted(REQUIRED_EVIDENCE_COLUMNS),),
            )
            actual_columns: dict[str, set[str]] = {}
            for row in await cursor.fetchall():
                actual_columns.setdefault(str(row["table_name"]), set()).add(
                    str(row["column_name"])
                )
    missing = sorted({"pg_search", "vector"} - extensions)
    for table, required in REQUIRED_EVIDENCE_COLUMNS.items():
        if not required.issubset(actual_columns.get(table, set())):
            missing.append(f"public.{table}:columns")
    if missing:
        raise EvidenceSchemaMissing("required evidence objects are unavailable")


async def _recorded_version(connection: AsyncConnection[dict[str, Any]]) -> str | None:
    async with connection.transaction():
        async with connection.cursor() as cursor:
            await cursor.execute("SELECT to_regclass('agent_runtime.schema_version') AS relation")
            row = await cursor.fetchone()
            if not isinstance(row, Mapping) or row.get("relation") is None:
                return None
            await cursor.execute(
                "SELECT version FROM agent_runtime.schema_version WHERE singleton = true"
            )
            version_row = await cursor.fetchone()
    if not isinstance(version_row, Mapping):
        return None
    value = version_row.get("version")
    return str(value) if value is not None else None


async def _create_roles_and_schemas(
    connection: AsyncConnection[dict[str, Any]],
    *,
    reader_password: str,
    writer_password: str,
) -> None:
    async with connection.cursor() as cursor:
        await cursor.execute(
            "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s::text[])",
            ([READER_ROLE, WRITER_ROLE],),
        )
        existing = {str(row["rolname"]) for row in await cursor.fetchall()}
        for role, password in (
            (READER_ROLE, reader_password),
            (WRITER_ROLE, writer_password),
        ):
            verb = "ALTER ROLE" if role in existing else "CREATE ROLE"
            await cursor.execute(
                sql.SQL(
                    f"{verb} {{role}} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {password}"
                ).format(role=sql.Identifier(role), password=sql.Literal(password))
            )
        await cursor.execute("CREATE SCHEMA IF NOT EXISTS agent_read")
        await cursor.execute("CREATE SCHEMA IF NOT EXISTS agent_runtime")


async def _create_views_and_version_table(connection: AsyncConnection[dict[str, Any]]) -> None:
    async with connection.cursor() as cursor:
        for statement in VIEW_DEFINITIONS.values():
            await cursor.execute(statement)
        await cursor.execute(
            "CREATE TABLE IF NOT EXISTS agent_runtime.schema_version ("
            "singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton), "
            "version text NOT NULL, "
            "applied_at timestamptz NOT NULL DEFAULT now()"
            ")"
        )


async def _setup_checkpointer(owner_dsn: str) -> None:
    connection = await AsyncConnection.connect(
        owner_dsn,
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
        options="-c search_path=agent_runtime,pg_catalog",
    )
    try:
        saver = AsyncPostgresSaver(connection)
        await saver.setup()
    finally:
        await connection.close()


async def _apply_least_privilege_grants(
    connection: AsyncConnection[dict[str, Any]],
) -> None:
    async with connection.cursor() as cursor:
        await cursor.execute("SELECT current_database() AS database_name")
        row = await cursor.fetchone()
        if not isinstance(row, Mapping) or not isinstance(row.get("database_name"), str):
            raise RuntimeError("could not resolve application database name")
        database = sql.Identifier(row["database_name"])

        await cursor.execute("REVOKE ALL ON SCHEMA agent_read, agent_runtime FROM PUBLIC")
        await cursor.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        await cursor.execute(
            sql.SQL("REVOKE TEMPORARY ON DATABASE {} FROM PUBLIC").format(database)
        )
        await cursor.execute(
            sql.SQL("REVOKE TEMPORARY ON DATABASE {} FROM {}, {}").format(
                database,
                sql.Identifier(READER_ROLE),
                sql.Identifier(WRITER_ROLE),
            )
        )
        await cursor.execute("REVOKE ALL ON SCHEMA agent_runtime FROM agent_reader")
        await cursor.execute("REVOKE ALL ON ALL TABLES IN SCHEMA agent_runtime FROM agent_reader")
        await cursor.execute("REVOKE ALL ON SCHEMA agent_read, public FROM agent_writer")
        await cursor.execute("REVOKE ALL ON ALL TABLES IN SCHEMA agent_read FROM agent_writer")
        await cursor.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM agent_writer")

        await cursor.execute("GRANT USAGE ON SCHEMA public, agent_read TO agent_reader")
        await cursor.execute(
            "GRANT SELECT ON public.records, public.chunks, public.entities, "
            "public.relationships, public.transactions, public.accounts, "
            "public.communications TO agent_reader"
        )
        await cursor.execute("GRANT SELECT ON ALL TABLES IN SCHEMA agent_read TO agent_reader")
        await cursor.execute("ALTER ROLE agent_reader SET default_transaction_read_only = on")
        await cursor.execute("ALTER ROLE agent_reader SET search_path = pg_catalog, public")

        await cursor.execute("GRANT USAGE ON SCHEMA agent_runtime TO agent_writer")
        await cursor.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
            "IN SCHEMA agent_runtime TO agent_writer"
        )
        await cursor.execute(
            "REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER "
            "ON agent_runtime.schema_version FROM agent_writer"
        )
        await cursor.execute("GRANT SELECT ON agent_runtime.schema_version TO agent_writer")
        await cursor.execute(
            "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA agent_runtime TO agent_writer"
        )
        await cursor.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA agent_runtime "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO agent_writer"
        )
        await cursor.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA agent_runtime "
            "GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO agent_writer"
        )
        await cursor.execute("ALTER ROLE agent_writer SET search_path = agent_runtime, pg_catalog")


def _native_dsn(dsn: str) -> str:
    prefix = "postgresql+psycopg://"
    return f"postgresql://{dsn.removeprefix(prefix)}" if dsn.startswith(prefix) else dsn


__all__ = [
    "EvidenceSchemaMissing",
    "INITIALIZER_SCHEMA_VERSION",
    "InitializationResult",
    "IncompatibleInitializerVersion",
    "READER_ROLE",
    "REQUIRED_EVIDENCE_INDEXES",
    "REQUIRED_EVIDENCE_COLUMNS",
    "REQUIRED_EVIDENCE_RELATIONS",
    "VIEW_DEFINITIONS",
    "WRITER_ROLE",
    "initialize_database",
]
