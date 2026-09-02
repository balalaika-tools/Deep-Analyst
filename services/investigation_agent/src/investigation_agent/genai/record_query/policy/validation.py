"""Complete-tree PostgreSQL AST validation for model-authored SELECT statements."""

from __future__ import annotations

import json
from typing import Any, NoReturn

from pglast import parse_sql, parser
from pglast.stream import RawStream

from investigation_agent.genai.record_query.policy.catalog import (
    ALLOWED_CASTS,
    ALLOWED_FUNCTIONS,
    ALLOWED_NODE_TYPES,
    ALLOWED_OPERATORS,
    PROVENANCE_COLUMNS,
    VIEW_COLUMNS,
)
from investigation_agent.genai.record_query.policy.contracts import (
    SqlPolicyViolation,
    ValidatedSelect,
)
from investigation_agent.genai.record_query.schemas import (
    ALLOWLIST_VERSION,
    DiagnosticClass,
    SqlPlan,
    digest_payload,
)


def validate_sql_plan(plan: SqlPlan) -> ValidatedSelect:
    """Parse and validate the complete PostgreSQL tree before database I/O."""

    _reject_sql_comments(plan.sql)
    document = _parse_json(plan.sql)
    statements = document.get("stmts")
    if not isinstance(statements, list) or len(statements) != 1:
        raise SqlPolicyViolation(
            "statement_count",
            "exactly one statement is required",
            diagnostic_class=DiagnosticClass.PARSE,
        )
    raw_statement = statements[0]
    if not isinstance(raw_statement, dict):
        raise _ambiguous_parse()
    statement = raw_statement.get("stmt")
    if not isinstance(statement, dict) or set(statement) != {"SelectStmt"}:
        raise SqlPolicyViolation(
            "select_required",
            "only a SELECT statement is allowed",
            diagnostic_class=DiagnosticClass.POLICY,
        )
    state = _PolicyState()
    state.collect_ctes(document)
    state.collect_relations(document)
    state.collect_target_aliases(document)
    state.walk(document)
    state.require_top_level_provenance(statement["SelectStmt"])
    if sorted(state.parameter_positions) != list(range(1, len(plan.parameters) + 1)):
        raise SqlPolicyViolation(
            "parameter_mismatch",
            "bound parameter positions do not exactly match the parsed statement",
            diagnostic_class=DiagnosticClass.POLICY,
        )
    try:
        parsed = parse_sql(plan.sql)
        canonical = RawStream()(parsed[0].stmt).strip().rstrip(";")  # type: ignore[no-untyped-call]
    except Exception as error:
        raise _ambiguous_parse() from error
    fingerprint = digest_payload(
        {
            "allowlist_version": ALLOWLIST_VERSION,
            "sql": canonical,
            "parameters": [item.model_dump(mode="json") for item in plan.parameters],
        }
    )
    return ValidatedSelect(
        canonical_sql=canonical,
        parameter_count=len(plan.parameters),
        referenced_views=tuple(sorted(state.referenced_views)),
        fingerprint=fingerprint,
    )


class _PolicyState:
    def __init__(self) -> None:
        self.cte_names: set[str] = set()
        self.alias_to_view: dict[str, str] = {}
        self.target_aliases: set[str] = set()
        self.referenced_views: set[str] = set()
        self.parameter_positions: set[int] = set()
        self._all_columns = {
            column for columns in VIEW_COLUMNS.values() for column, _data_type in columns
        }

    def collect_ctes(self, document: object) -> None:
        for payload in _node_payloads(document, "CommonTableExpr"):
            name = payload.get("ctename")
            if not isinstance(name, str) or not name:
                raise _ambiguous_parse()
            self.cte_names.add(name)

    def collect_relations(self, document: object) -> None:
        """Resolve every RangeVar against the CTEs PostgreSQL would actually see there.

        A CTE name is visible only inside the SELECT it is attached to (and its
        subqueries) and inside later CTEs of the same WITH list; a CTE body never
        sees its own name. Anything else must be an allowlisted schema-qualified view.
        """

        self._collect_scoped_relations(document, frozenset())

    def _collect_scoped_relations(self, value: object, visible_ctes: frozenset[str]) -> None:
        if isinstance(value, list):
            for item in value:
                self._collect_scoped_relations(item, visible_ctes)
            return
        if not isinstance(value, dict):
            return
        for key, payload in value.items():
            if key == "SelectStmt" and isinstance(payload, dict) and "withClause" in payload:
                self._collect_with_scope(payload, visible_ctes)
            elif key == "RangeVar" and isinstance(payload, dict):
                self._register_relation(payload, visible_ctes)
            else:
                self._collect_scoped_relations(payload, visible_ctes)

    def _collect_with_scope(self, select: dict[str, Any], visible_ctes: frozenset[str]) -> None:
        with_clause = select["withClause"]
        if not isinstance(with_clause, dict):
            raise _ambiguous_parse()
        if with_clause.get("recursive", False):
            raise SqlPolicyViolation(
                "recursive_cte",
                "recursive CTEs are not allowed",
                diagnostic_class=DiagnosticClass.POLICY,
            )
        scope = visible_ctes
        for entry in with_clause.get("ctes") or ():
            cte = entry.get("CommonTableExpr") if isinstance(entry, dict) else None
            if not isinstance(cte, dict) or not isinstance(cte.get("ctename"), str):
                raise _ambiguous_parse()
            self._collect_scoped_relations(cte.get("ctequery"), scope)
            scope = scope | {cte["ctename"]}
        body = {key: payload for key, payload in select.items() if key != "withClause"}
        self._collect_scoped_relations(body, scope)

    def _register_relation(self, payload: dict[str, Any], visible_ctes: frozenset[str]) -> None:
        schema = payload.get("schemaname")
        relation = payload.get("relname")
        if not isinstance(relation, str):
            raise _ambiguous_parse()
        if schema is None and relation in visible_ctes:
            return
        if schema != "agent_read" or relation not in VIEW_COLUMNS:
            raise SqlPolicyViolation(
                "relation_not_allowed",
                "every base relation must be an allowlisted schema-qualified view",
                diagnostic_class=DiagnosticClass.SCHEMA,
            )
        self.referenced_views.add(relation)
        self.alias_to_view[relation] = relation
        alias = payload.get("alias")
        if isinstance(alias, dict):
            alias_payload = alias.get("Alias", alias)
            if isinstance(alias_payload, dict) and isinstance(alias_payload.get("aliasname"), str):
                self.alias_to_view[alias_payload["aliasname"]] = relation

    def collect_target_aliases(self, document: object) -> None:
        for payload in _node_payloads(document, "ResTarget"):
            name = payload.get("name")
            if isinstance(name, str):
                self.target_aliases.add(name)

    def walk(self, value: object) -> None:
        if isinstance(value, list):
            for item in value:
                self.walk(item)
            return
        if not isinstance(value, dict):
            return
        for key, payload in value.items():
            if _looks_like_node_type(key):
                if key not in ALLOWED_NODE_TYPES:
                    raise SqlPolicyViolation(
                        "unknown_ast_node",
                        "statement contains an unsupported SQL construct",
                        diagnostic_class=DiagnosticClass.POLICY,
                    )
                if not isinstance(payload, dict):
                    raise _ambiguous_parse()
                self._validate_node(key, payload)
            self.walk(payload)

    def _validate_node(self, node_type: str, payload: dict[str, Any]) -> None:
        validator = {
            "SelectStmt": self._validate_select,
            "RangeVar": self._validate_range_var,
            "ColumnRef": self._validate_column,
            "FuncCall": self._validate_function,
            "A_Expr": self._validate_operator,
            "TypeName": self._validate_type,
            "ParamRef": self._validate_parameter,
        }.get(node_type)
        if validator is not None:
            validator(payload)

    def _validate_select(self, payload: dict[str, Any]) -> None:
        checks = (
            (payload.get("intoClause") is not None, "select_into", "SELECT INTO is not allowed"),
            (bool(payload.get("lockingClause")), "row_lock", "row locking is not allowed"),
            (bool(payload.get("valuesLists")), "values_query", "VALUES queries are not allowed"),
            (
                payload.get("op", 0) not in (0, "SETOP_NONE"),
                "set_operation",
                "set operations are not allowlisted",
            ),
        )
        for rejected, code, detail in checks:
            if rejected:
                raise SqlPolicyViolation(code, detail, diagnostic_class=DiagnosticClass.POLICY)

    def _validate_range_var(self, payload: dict[str, Any]) -> None:
        if payload.get("relpersistence", "p") not in ("p", 112):
            raise SqlPolicyViolation(
                "temporary_relation",
                "temporary or unlogged relations are not allowed",
                diagnostic_class=DiagnosticClass.POLICY,
            )

    def _validate_column(self, payload: dict[str, Any]) -> None:
        fields = _string_fields(payload.get("fields"))
        if not fields:
            raise SqlPolicyViolation(
                "star_not_allowed",
                "wildcard or ambiguous columns are not allowed",
                diagnostic_class=DiagnosticClass.SCHEMA,
            )
        column = fields[-1]
        if len(fields) == 1:
            if column not in self._all_columns and column not in self.target_aliases:
                self._unknown_column()
            return
        if len(fields) == 2:
            prefix = fields[0]
            view = self.alias_to_view.get(prefix)
            if view is None:
                if prefix in self.cte_names and column in self._all_columns | self.target_aliases:
                    return
                self._unknown_column()
            assert view is not None
            if column not in {name for name, _data_type in VIEW_COLUMNS[view]}:
                self._unknown_column()
            return
        if len(fields) == 3 and fields[0] == "agent_read":
            view = fields[1]
            if view in VIEW_COLUMNS and column in {name for name, _data_type in VIEW_COLUMNS[view]}:
                return
        self._unknown_column()

    def _validate_function(self, payload: dict[str, Any]) -> None:
        names = _string_fields(payload.get("funcname"))
        if len(names) != 1 or names[0].casefold() not in ALLOWED_FUNCTIONS:
            raise SqlPolicyViolation(
                "function_not_allowed",
                "statement calls a function outside the allowlist",
                diagnostic_class=DiagnosticClass.POLICY,
            )

    def _validate_operator(self, payload: dict[str, Any]) -> None:
        names = _string_fields(payload.get("name"))
        if not names or names[-1] not in ALLOWED_OPERATORS:
            raise SqlPolicyViolation(
                "operator_not_allowed",
                "statement uses an operator outside the allowlist",
                diagnostic_class=DiagnosticClass.POLICY,
            )

    def _validate_type(self, payload: dict[str, Any]) -> None:
        names = _string_fields(payload.get("names"))
        if (names[-1].casefold() if names else "") not in ALLOWED_CASTS:
            raise SqlPolicyViolation(
                "cast_not_allowed",
                "statement uses a cast outside the allowlist",
                diagnostic_class=DiagnosticClass.POLICY,
            )

    def _validate_parameter(self, payload: dict[str, Any]) -> None:
        number = payload.get("number")
        if not isinstance(number, int) or number < 1:
            raise _ambiguous_parse()
        self.parameter_positions.add(number)

    def require_top_level_provenance(self, payload: object) -> None:
        if not isinstance(payload, dict):
            raise _ambiguous_parse()
        target_list = payload.get("targetList")
        if not isinstance(target_list, list):
            self._provenance_required()
        direct: set[str] = set()
        for target in target_list:
            if not isinstance(target, dict):
                continue
            result_target = target.get("ResTarget")
            if not isinstance(result_target, dict):
                continue
            value = result_target.get("val")
            column_ref = value.get("ColumnRef") if isinstance(value, dict) else None
            if not isinstance(column_ref, dict):
                continue
            fields = _string_fields(column_ref.get("fields"))
            alias = result_target.get("name")
            if fields and fields[-1] in PROVENANCE_COLUMNS and alias in (None, fields[-1]):
                direct.add(fields[-1])
        if not PROVENANCE_COLUMNS.issubset(direct):
            self._provenance_required()

    @staticmethod
    def _unknown_column() -> NoReturn:
        raise SqlPolicyViolation(
            "column_not_allowed",
            "statement references a column outside the allowlist",
            diagnostic_class=DiagnosticClass.SCHEMA,
        )

    @staticmethod
    def _provenance_required() -> NoReturn:
        raise SqlPolicyViolation(
            "provenance_required",
            "provenance requires direct record_id, content_hash, and source_refs",
            diagnostic_class=DiagnosticClass.POLICY,
        )


def _parse_json(sql: str) -> dict[str, Any]:
    try:
        parsed = json.loads(parser.parse_sql_json(sql))
    except Exception as error:
        raise SqlPolicyViolation(
            "parse_failed",
            "statement is not valid unambiguous PostgreSQL SQL",
            diagnostic_class=DiagnosticClass.PARSE,
        ) from error
    if not isinstance(parsed, dict):
        raise _ambiguous_parse()
    return parsed


def _reject_sql_comments(sql: str) -> None:
    state = "plain"
    index = 0
    while index < len(sql):
        current = sql[index]
        following = sql[index + 1] if index + 1 < len(sql) else ""
        if state in {"single", "double"}:
            quote = "'" if state == "single" else '"'
            if current == quote and following == quote:
                index += 2
                continue
            if current == quote:
                state = "plain"
            index += 1
            continue
        if current == "'":
            state = "single"
        elif current == '"':
            state = "double"
        elif (current, following) in {("-", "-"), ("/", "*")}:
            raise SqlPolicyViolation(
                "comments_not_allowed",
                "SQL comments are not allowed",
                diagnostic_class=DiagnosticClass.POLICY,
            )
        index += 1


def _looks_like_node_type(value: str) -> bool:
    return bool(value) and (value[0].isupper() or value.startswith("A_"))


def _node_payloads(value: object, node_type: str) -> tuple[dict[str, Any], ...]:
    found: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            found.extend(_node_payloads(item, node_type))
    elif isinstance(value, dict):
        for key, payload in value.items():
            if key == node_type and isinstance(payload, dict):
                found.append(payload)
            found.extend(_node_payloads(payload, node_type))
    return tuple(found)


def _string_fields(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    fields: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            return ()
        payload = item.get("String")
        if not isinstance(payload, dict):
            return ()
        scalar = payload.get("sval", payload.get("str"))
        if not isinstance(scalar, str):
            return ()
        fields.append(scalar)
    return tuple(fields)


def _ambiguous_parse() -> SqlPolicyViolation:
    return SqlPolicyViolation(
        "ambiguous_parse",
        "parser returned an unsupported or ambiguous tree",
        diagnostic_class=DiagnosticClass.PARSE,
    )


__all__ = ["validate_sql_plan"]
