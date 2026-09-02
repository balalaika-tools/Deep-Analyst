from __future__ import annotations

import json
from typing import Any

from investigation_agent.adapters.postgres.initializer import VIEW_DEFINITIONS
from investigation_agent.genai.record_query.policy import VIEW_COLUMNS
from pglast import parser


def test_view_contract_projects_exact_allowlist_and_enforces_server_scope() -> None:
    assert VIEW_DEFINITIONS.keys() == VIEW_COLUMNS.keys()
    for view_name, statement in VIEW_DEFINITIONS.items():
        assert "current_setting('app.case_id', true)" in statement
        assert "security_invoker = true" in statement
        parsed = json.loads(parser.parse_sql_json(statement))
        view = parsed["stmts"][0]["stmt"]["ViewStmt"]
        query = view["query"]["SelectStmt"]
        actual = tuple(_target_name(target) for target in query["targetList"])
        expected = tuple(column for column, _data_type in VIEW_COLUMNS[view_name])
        assert actual == expected


def _target_name(target: dict[str, Any]) -> str:
    payload = target["ResTarget"]
    alias = payload.get("name")
    if isinstance(alias, str):
        return alias
    fields = payload["val"]["ColumnRef"]["fields"]
    return str(fields[-1]["String"]["sval"])
