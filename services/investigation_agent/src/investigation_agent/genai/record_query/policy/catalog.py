"""Versioned model-visible schema and SQL construct allowlists."""

from investigation_agent.genai.record_query.schemas import (
    SchemaColumn,
    SchemaView,
    ServerSchemaDescription,
)

PROVENANCE_COLUMNS = frozenset({"record_id", "case_id", "content_hash", "source_refs"})

VIEW_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "transactions_v1": (
        ("record_id", "text"),
        ("case_id", "text"),
        ("source_system", "text"),
        ("source_record_id", "text"),
        ("source_path", "text"),
        ("content_hash", "text"),
        ("source_refs", "jsonb"),
        ("txn_id", "text"),
        ("booking_ts_utc", "timestamptz"),
        ("value_date", "date"),
        ("debtor_iban", "text"),
        ("debtor_name", "text"),
        ("creditor_iban", "text"),
        ("creditor_name", "text"),
        ("amount_minor", "bigint"),
        ("amount_text", "text"),
        ("currency", "text"),
        ("status", "text"),
        ("remittance_info", "text"),
    ),
    "accounts_v1": (
        ("record_id", "text"),
        ("case_id", "text"),
        ("source_system", "text"),
        ("source_record_id", "text"),
        ("source_path", "text"),
        ("content_hash", "text"),
        ("source_refs", "jsonb"),
        ("account_id", "text"),
        ("iban", "text"),
        ("holder_name", "text"),
        ("holder_type", "text"),
        ("bic", "text"),
        ("opened_date", "date"),
    ),
    "communications_v1": (
        ("record_id", "text"),
        ("case_id", "text"),
        ("source_system", "text"),
        ("source_record_id", "text"),
        ("source_path", "text"),
        ("content_hash", "text"),
        ("source_refs", "jsonb"),
        ("channel", "text"),
        ("direction", "text"),
        ("from_endpoint", "text"),
        ("to_endpoint", "text"),
        ("event_time_utc", "timestamptz"),
        ("original_time", "text"),
        ("duration_s", "integer"),
        ("device_id", "text"),
    ),
}

ALLOWED_FUNCTIONS = frozenset(
    {
        "abs",
        "avg",
        "coalesce",
        "count",
        "date_trunc",
        "greatest",
        "least",
        "lower",
        "max",
        "min",
        "nullif",
        "round",
        "sum",
        "upper",
    }
)
ALLOWED_OPERATORS = frozenset(
    {
        "=",
        "<>",
        "<",
        "<=",
        ">",
        ">=",
        "+",
        "-",
        "*",
        "/",
        "~~",
        "!~~",
        "~~*",
        "!~~*",
        "BETWEEN",
        "NOT BETWEEN",
    }
)
ALLOWED_CASTS = frozenset(
    {"bool", "date", "int2", "int4", "int8", "numeric", "text", "timestamp", "timestamptz"}
)
ALLOWED_NODE_TYPES = frozenset(
    {
        "A_ArrayExpr",
        "A_Expr",
        "Alias",
        "BoolExpr",
        "BooleanTest",
        "CaseExpr",
        "CaseWhen",
        "CoalesceExpr",
        "CollateClause",
        "ColumnRef",
        "CommonTableExpr",
        "Float",
        "FuncCall",
        "Integer",
        "JoinExpr",
        "List",
        "MinMaxExpr",
        "Null",
        "NullTest",
        "ParamRef",
        "RangeSubselect",
        "RangeVar",
        "ResTarget",
        "RowExpr",
        "SelectStmt",
        "SortBy",
        "String",
        "SubLink",
        "TypeCast",
        "TypeName",
        "WithClause",
        "WindowDef",
    }
)


def schema_description() -> ServerSchemaDescription:
    return ServerSchemaDescription(
        views=tuple(
            SchemaView(
                name=f"agent_read.{name}",
                columns=tuple(
                    SchemaColumn(name=column, data_type=data_type) for column, data_type in columns
                ),
            )
            for name, columns in VIEW_COLUMNS.items()
        ),
        allowed_functions=tuple(sorted(ALLOWED_FUNCTIONS)),
        allowed_operators=tuple(sorted(ALLOWED_OPERATORS)),
    )


__all__ = [
    "ALLOWED_CASTS",
    "ALLOWED_FUNCTIONS",
    "ALLOWED_NODE_TYPES",
    "ALLOWED_OPERATORS",
    "PROVENANCE_COLUMNS",
    "VIEW_COLUMNS",
    "schema_description",
]
