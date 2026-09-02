"""``query_records`` nested agent: one ``execute_sql`` tool behind the policy gate."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, cast

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain.tools import ToolRuntime
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from pydantic import ValidationError

from investigation_agent.genai.guardrails.middleware import normalize_untrusted_text
from investigation_agent.genai.record_query.executor import (
    ExecutorLimits,
    ReaderPool,
    execute_guarded_select,
)
from investigation_agent.genai.record_query.policy import (
    SqlPolicyViolation,
    schema_description,
    validate_sql_plan,
)
from investigation_agent.genai.record_query.prompts import QUERY_AGENT_SYSTEM_PROMPT
from investigation_agent.genai.record_query.schemas import (
    MAX_SEMANTIC_ATTEMPTS,
    DiagnosticClass,
    GuardedSelectResult,
    QueryAttempt,
    QueryConsumption,
    QueryIntent,
    QueryOutcome,
    QueryVerdict,
    SafeDiagnostic,
    SqlPlan,
    StructuredRowEvidence,
    digest_payload,
)
from investigation_agent.genai.shared.retries import (
    CancellationToken,
    RetryPolicy,
    model_retry_middleware,
    tool_retry_middleware,
)

type ProgressWriter = Callable[[Mapping[str, object]], None]


class QueryTransientError(ConnectionError):
    """One physical execution failed transiently; retry the identical approved plan."""


@dataclass(slots=True)
class QueryInvocation:
    """Invocation-local, trusted state the nested model can neither read nor write."""

    deadline: float
    cancellation: CancellationToken
    limits: ExecutorLimits
    fingerprints: set[str] = field(default_factory=set)
    attempts: list[QueryAttempt] = field(default_factory=list)
    rows: dict[str, StructuredRowEvidence] = field(default_factory=dict)
    physical_attempts: int = 0
    rows_seen: int = 0
    encoded_bytes: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class QueryAgentPolicy:
    model_call_limit: int = 6
    tool_call_limit: int = MAX_SEMANTIC_ATTEMPTS
    max_evidence: int = 200

    def __post_init__(self) -> None:
        if not 1 <= self.tool_call_limit <= MAX_SEMANTIC_ATTEMPTS:
            raise ValueError("execute_sql calls must be between one and three")
        if self.model_call_limit < 1 or self.max_evidence < 1:
            raise ValueError("query agent bounds must be positive")


class QueryRecordsAgent:
    """Keeps authored SQL private and returns one bounded typed evidence outcome."""

    def __init__(
        self,
        *,
        model: Any,
        reader_pool: ReaderPool,
        executor_limits: ExecutorLimits,
        retry_policy: RetryPolicy,
        transient_errors: tuple[type[Exception], ...],
        policy: QueryAgentPolicy | None = None,
    ) -> None:
        self._reader_pool = reader_pool
        # Physical retries are owned by the tool retry middleware, not the executor.
        self._executor_limits = replace(executor_limits, max_physical_attempts=1)
        self._policy = policy or QueryAgentPolicy()
        self._schema = schema_description()
        self._agent = create_agent(
            model=model,
            tools=[self._build_execute_tool()],
            system_prompt=QUERY_AGENT_SYSTEM_PROMPT,
            response_format=QueryVerdict,
            context_schema=QueryInvocation,
            middleware=[
                model_retry_middleware(retry_policy, transient_errors),
                tool_retry_middleware(
                    retry_policy, (QueryTransientError, *transient_errors), tools=["execute_sql"]
                ),
                cast(
                    Any,
                    ModelCallLimitMiddleware(
                        run_limit=self._policy.model_call_limit, exit_behavior="end"
                    ),
                ),
                cast(
                    Any,
                    ToolCallLimitMiddleware(
                        run_limit=self._policy.tool_call_limit, exit_behavior="continue"
                    ),
                ),
            ],
            checkpointer=None,
        )

    def _build_execute_tool(self) -> Any:
        pool = self._reader_pool

        @tool
        async def execute_sql(
            sql: str,
            runtime: ToolRuntime[QueryInvocation],
            parameters: list[dict[str, Any]] | None = None,
        ) -> str:
            """Run one parameterized read-only SELECT over agent_read views.

            ``parameters`` is a list of ``{"position", "parameter_type", "value"}`` entries.
            """

            invocation = runtime.context
            try:
                plan = SqlPlan.model_validate({"sql": sql, "parameters": parameters or []})
            except ValidationError:
                return _failure_message(
                    SafeDiagnostic(diagnostic_class=DiagnosticClass.PARSE, code="invalid_plan")
                )
            if len(invocation.attempts) >= MAX_SEMANTIC_ATTEMPTS:
                return _failure_message(
                    SafeDiagnostic(
                        diagnostic_class=DiagnosticClass.RESOURCE_LIMIT,
                        code="attempts_exhausted",
                        correctable=False,
                    )
                )
            try:
                validated = validate_sql_plan(plan)
            except SqlPolicyViolation as violation:
                fingerprint = digest_payload({"rejected": plan.model_dump(mode="json")})
                return _record_rejection(invocation, fingerprint, violation.diagnostic)
            fingerprint = digest_payload(
                {
                    "canonical": validated.fingerprint,
                    "parameters": plan.model_dump(mode="json")["parameters"],
                }
            )
            if fingerprint in invocation.fingerprints:
                invocation.warnings.append("repeated_plan_rejected")
                return _failure_message(
                    SafeDiagnostic(diagnostic_class=DiagnosticClass.POLICY, code="repeated_plan")
                )
            invocation.cancellation.check()
            invocation.physical_attempts += 1
            result = await execute_guarded_select(
                pool=pool,
                plan=plan,
                deadline=invocation.deadline,
                limits=invocation.limits,
            )
            if _is_transient_failure(result):
                raise QueryTransientError("transient database failure during guarded select")
            return _record_result(invocation, fingerprint, result, runtime)

        return execute_sql

    async def run(
        self,
        intent: QueryIntent,
        *,
        call_id: str,
        deadline: float,
        cancellation: CancellationToken,
        progress: ProgressWriter | None = None,
    ) -> QueryOutcome:
        invocation = QueryInvocation(
            deadline=deadline,
            cancellation=cancellation,
            limits=self._executor_limits,
        )
        payload = {
            "intent": intent.model_dump(mode="json"),
            "schema": self._schema.model_dump(mode="json"),
        }
        verdict: QueryVerdict | None = None
        model_calls = 0
        async for part in self._agent.astream(
            {"messages": [HumanMessage(content=json.dumps(payload, sort_keys=True))]},
            context=invocation,
            stream_mode=["updates", "custom"],
            version="v2",
        ):
            if part["type"] == "custom":
                if progress is not None:
                    progress(dict(part["data"]))
                continue
            update = cast(dict[str, Any], part["data"])
            if "model" in update:
                model_calls += 1
                structured = update["model"].get("structured_response")
                if structured is not None:
                    verdict = QueryVerdict.model_validate(structured)
        return _outcome(
            invocation,
            verdict,
            call_id=call_id,
            intent_fingerprint=digest_payload(intent.model_dump(mode="json")),
            model_calls=model_calls,
            max_evidence=self._policy.max_evidence,
        )


def _record_rejection(
    invocation: QueryInvocation, fingerprint: str, diagnostic: SafeDiagnostic
) -> str:
    if fingerprint in invocation.fingerprints:
        invocation.warnings.append("repeated_plan_rejected")
        return _failure_message(
            SafeDiagnostic(diagnostic_class=DiagnosticClass.POLICY, code="repeated_plan")
        )
    invocation.fingerprints.add(fingerprint)
    invocation.attempts.append(
        QueryAttempt(
            semantic_attempt=len(invocation.attempts) + 1,
            plan_fingerprint=fingerprint,
            outcome="rejected",
            diagnostic=diagnostic,
        )
    )
    return _failure_message(diagnostic)


def _record_result(
    invocation: QueryInvocation,
    fingerprint: str,
    result: GuardedSelectResult,
    runtime: ToolRuntime[QueryInvocation],
) -> str:
    invocation.fingerprints.add(fingerprint)
    attempt_number = len(invocation.attempts) + 1
    outcome = "ok" if result.status == "ok" else ("empty" if result.status == "empty" else "failed")
    invocation.attempts.append(
        QueryAttempt(
            semantic_attempt=attempt_number,
            plan_fingerprint=fingerprint,
            physical_attempts=invocation.physical_attempts,
            outcome=outcome,
            diagnostic=result.diagnostic,
            row_count=len(result.rows),
        )
    )
    invocation.physical_attempts = 0
    invocation.rows_seen += result.rows_seen
    invocation.encoded_bytes += result.encoded_bytes
    for row in result.rows:
        invocation.rows.setdefault(row.evidence_id, row)
    runtime.stream_writer(
        {"phase": "querying_records", "tool": "query_records", "attempt": attempt_number}
    )
    if result.status == "ok":
        return _rows_message(result)
    diagnostic = result.diagnostic or SafeDiagnostic(
        diagnostic_class=DiagnosticClass.EMPTY, code="empty_result"
    )
    return _failure_message(diagnostic)


def _outcome(
    invocation: QueryInvocation,
    verdict: QueryVerdict | None,
    *,
    call_id: str,
    intent_fingerprint: str,
    model_calls: int,
    max_evidence: int,
) -> QueryOutcome:
    warnings = list(invocation.warnings)
    if verdict is None:
        status = "query_exhausted"
        selected: list[str] = []
        warnings.append("nested_agent_limit_reached")
    else:
        status = verdict.status
        selected = sorted(set(verdict.selected_row_ids) & set(invocation.rows))
        if set(verdict.selected_row_ids) - set(invocation.rows):
            warnings.append("unreturned_selection_dropped")
        if status == "query_sufficient" and not selected:
            status = "query_exhausted"
            warnings.append("sufficient_without_selection")
    evidence = tuple(invocation.rows[row_id] for row_id in selected[:max_evidence])
    if len(selected) > max_evidence:
        warnings.append("evidence_bound_reached")
    return QueryOutcome(
        call_id=call_id,
        intent_fingerprint=intent_fingerprint,
        status=status,
        attempts=tuple(invocation.attempts),
        evidence=evidence,
        warnings=tuple(dict.fromkeys(warnings))[:16],
        consumption=QueryConsumption(
            model_calls=model_calls,
            tool_calls=len(invocation.attempts),
            rows=invocation.rows_seen,
            bytes=invocation.encoded_bytes,
            physical_attempts=sum(a.physical_attempts for a in invocation.attempts),
        ),
    )


def _is_transient_failure(result: GuardedSelectResult) -> bool:
    return (
        result.status == "failed"
        and result.diagnostic is not None
        and result.diagnostic.code == "transient_exhausted"
    )


def _rows_message(result: GuardedSelectResult) -> str:
    header = {"row_count": len(result.rows), "truncated": result.truncated}
    parts = [json.dumps(header, sort_keys=True)]
    for row in result.rows:
        rendered = "\n".join(
            f"{field.name}: {normalize_untrusted_text(str(field.value))}" for field in row.fields
        )
        parts.append(
            f"<untrusted-evidence id={row.evidence_id!r}>\n{rendered}\n</untrusted-evidence>"
        )
    return "\n".join(parts)


def _failure_message(diagnostic: SafeDiagnostic) -> str:
    return json.dumps(
        {
            "failure_class": diagnostic.diagnostic_class.value,
            "code": diagnostic.code,
            "detail": diagnostic.detail,
            "correctable": diagnostic.correctable,
        },
        sort_keys=True,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


__all__ = ["QueryAgentPolicy", "QueryInvocation", "QueryRecordsAgent", "QueryTransientError"]
