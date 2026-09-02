from __future__ import annotations

import pytest
from investigation_agent.genai.record_query.policy import (
    SqlPolicyViolation,
    validate_sql_plan,
)
from investigation_agent.genai.record_query.schemas import (
    ParameterType,
    SqlParameter,
    SqlPlan,
)

SAFE_SQL = (
    "SELECT record_id, case_id, content_hash, source_refs, amount_minor "
    "FROM agent_read.transactions_v1 WHERE amount_minor >= $1"
)


def plan(sql: str, *, with_parameter: bool = False) -> SqlPlan:
    parameters = (
        (SqlParameter(position=1, parameter_type=ParameterType.INTEGER, value=100),)
        if with_parameter
        else ()
    )
    return SqlPlan(sql=sql, parameters=parameters, expected_shape="bounded source rows")


def test_accepts_one_parameterized_select_and_fingerprint_covers_values() -> None:
    first = validate_sql_plan(plan(SAFE_SQL, with_parameter=True))
    second = validate_sql_plan(
        SqlPlan(
            sql=SAFE_SQL,
            parameters=(SqlParameter(position=1, parameter_type=ParameterType.INTEGER, value=101),),
            expected_shape="bounded source rows",
        )
    )

    assert first.canonical_sql == SAFE_SQL
    assert first.referenced_views == ("transactions_v1",)
    assert first.fingerprint != second.fingerprint


@pytest.mark.parametrize(
    "sql",
    [
        SAFE_SQL + "; SELECT 1",
        "INSERT INTO agent_read.transactions_v1 (record_id) VALUES ($1)",
        "COPY agent_read.transactions_v1 TO STDOUT",
        "SET app.case_id = 'other'",
        "SELECT record_id, case_id, content_hash, source_refs "
        "FROM agent_read.transactions_v1 INTO TEMP stolen",
        "SELECT record_id, case_id, content_hash, source_refs FROM transactions_v1",
        "SELECT record_id, case_id, content_hash, source_refs FROM pg_catalog.pg_class",
        "SELECT record_id, case_id, content_hash, source_refs FROM pg_temp.transactions_v1",
        "SELECT record_id, case_id, content_hash, source_refs, current_setting($1) "
        "FROM agent_read.transactions_v1",
        "SELECT record_id, case_id, content_hash, source_refs, set_config($1, $2, true) "
        "FROM agent_read.transactions_v1",
        "SELECT record_id, case_id, content_hash, source_refs "
        "FROM agent_read.transactions_v1 FOR UPDATE",
        "SELECT record_id, case_id, content_hash, source_refs "
        "FROM agent_read.transactions_v1 -- hidden statement",
        "SELECT record_id, case_id, content_hash, source_refs "
        "FROM agent_read.transactions_v1 /* hidden statement */",
        "SELECT record_id, case_id, content_hash, source_refs, 'interpolated' "
        "FROM agent_read.transactions_v1",
        "WITH changed AS (DELETE FROM agent_read.transactions_v1 RETURNING record_id) "
        "SELECT record_id, case_id, content_hash, source_refs FROM changed",
    ],
    ids=[
        "multiple-statements",
        "dml",
        "copy",
        "set",
        "select-into",
        "unqualified",
        "catalog",
        "temporary-schema",
        "session-read",
        "session-write",
        "row-lock",
        "line-comment",
        "block-comment",
        "literal-interpolation",
        "data-changing-cte",
    ],
)
def test_adversarial_sql_is_rejected(sql: str) -> None:
    parameter_count = sql.count("$1") + (1 if "$2" in sql else 0)
    parameters = tuple(
        SqlParameter(position=index, parameter_type=ParameterType.TEXT, value="x")
        for index in range(1, parameter_count + 1)
    )

    with pytest.raises(SqlPolicyViolation):
        validate_sql_plan(SqlPlan(sql=sql, parameters=parameters, expected_shape="rows"))


def test_direct_provenance_columns_are_mandatory() -> None:
    with pytest.raises(SqlPolicyViolation, match="provenance"):
        validate_sql_plan(
            plan(
                "SELECT record_id, case_id, content_hash, amount_minor "
                "FROM agent_read.transactions_v1"
            )
        )
