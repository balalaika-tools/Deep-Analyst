"""Guarded reader-pool execution for an already bounded structured SQL plan."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import psycopg
from evidence_model import FieldLocator, SourceRef, TextSpanLocator
from pydantic import ValidationError

from investigation_agent.genai.record_query.policy import SqlPolicyViolation, validate_sql_plan
from investigation_agent.genai.record_query.schemas import (
    DiagnosticClass,
    GuardedSelectResult,
    ResultField,
    SafeDiagnostic,
    SqlPlan,
    StructuredRowEvidence,
)


class AsyncCursor(Protocol):
    async def execute(self, query: str, params: Sequence[object] | None = None) -> object: ...

    async def fetchmany(self, size: int = 0) -> Sequence[Mapping[str, object]]: ...

    async def fetchall(self) -> Sequence[Mapping[str, object]]: ...

    async def __aenter__(self) -> AsyncCursor: ...

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None: ...


class AsyncConnection(Protocol):
    def cursor(self) -> AsyncCursor: ...

    def transaction(self) -> Any: ...


class ConnectionLease(Protocol):
    async def __aenter__(self) -> AsyncConnection: ...

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None: ...


class ReaderPool(Protocol):
    def connection(self, timeout: float | None = None) -> ConnectionLease: ...


@dataclass(frozen=True, slots=True)
class ExecutorLimits:
    max_rows: int = 200
    max_bytes: int = 1_000_000
    statement_timeout_ms: int = 5_000
    lock_timeout_ms: int = 250
    idle_transaction_timeout_ms: int = 5_000
    acquisition_timeout_s: float = 2.0
    max_physical_attempts: int = 2
    fetch_batch_size: int = 64

    def __post_init__(self) -> None:
        positive = (
            self.max_rows,
            self.max_bytes,
            self.statement_timeout_ms,
            self.lock_timeout_ms,
            self.idle_transaction_timeout_ms,
            self.max_physical_attempts,
            self.fetch_batch_size,
        )
        if any(item < 1 for item in positive) or self.acquisition_timeout_s <= 0:
            raise ValueError("executor limits must be positive")


@dataclass(frozen=True, slots=True)
class _CanonicalRecord:
    record_id: str
    case_id: str
    content_hash: str
    text: str | None
    payload: Mapping[str, object]


async def execute_guarded_select(
    *,
    pool: ReaderPool,
    case_id: str,
    plan: SqlPlan,
    deadline: float,
    limits: ExecutorLimits | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> GuardedSelectResult:
    """Validate before checkout, then execute under transaction-local trusted controls."""

    limits = limits or ExecutorLimits()
    try:
        validated = validate_sql_plan(plan)
    except SqlPolicyViolation as error:
        return GuardedSelectResult(status="rejected", diagnostic=error.diagnostic)

    attempts = 0
    while attempts < limits.max_physical_attempts:
        attempts += 1
        try:
            result = await _execute_once(
                pool=pool,
                case_id=case_id,
                canonical_sql=validated.canonical_sql,
                parameter_values=plan.parameter_values(),
                parameter_count=validated.parameter_count,
                deadline=deadline,
                limits=limits,
            )
            return result.model_copy(update={"physical_attempts": attempts})
        except asyncio.CancelledError:
            return GuardedSelectResult(
                status="cancelled",
                diagnostic=SafeDiagnostic(
                    diagnostic_class=DiagnosticClass.CANCELLED,
                    code="cancelled",
                    correctable=False,
                ),
                physical_attempts=attempts,
            )
        except Exception as error:
            if _is_transient(error) and attempts < limits.max_physical_attempts:
                await sleep(min(0.05 * (2 ** (attempts - 1)), _remaining(deadline)))
                continue
            return GuardedSelectResult(
                status="failed",
                diagnostic=_safe_database_diagnostic(error),
                physical_attempts=attempts,
            )
    raise AssertionError("bounded physical execution loop did not terminate")


async def _execute_once(
    *,
    pool: ReaderPool,
    case_id: str,
    canonical_sql: str,
    parameter_values: tuple[object, ...],
    parameter_count: int,
    deadline: float,
    limits: ExecutorLimits,
) -> GuardedSelectResult:
    timeout = min(limits.acquisition_timeout_s, _remaining(deadline))
    async with pool.connection(timeout=timeout) as connection:
        async with connection.transaction():
            async with connection.cursor() as cursor:
                await cursor.execute("SET TRANSACTION READ ONLY")
                await cursor.execute(
                    "SELECT set_config('app.case_id', $1, true), "
                    "set_config('statement_timeout', $2, true), "
                    "set_config('lock_timeout', $3, true), "
                    "set_config('idle_in_transaction_session_timeout', $4, true)",
                    (
                        case_id,
                        f"{limits.statement_timeout_ms}ms",
                        f"{limits.lock_timeout_ms}ms",
                        f"{limits.idle_transaction_timeout_ms}ms",
                    ),
                )
                limit_position = parameter_count + 1
                wrapped = f"SELECT * FROM ({canonical_sql}) AS agent_result LIMIT ${limit_position}"
                await cursor.execute(wrapped, (*parameter_values, limits.max_rows + 1))
                raw_rows, encoded_bytes, truncated = await _read_bounded_rows(
                    cursor,
                    max_rows=limits.max_rows,
                    max_bytes=limits.max_bytes,
                    batch_size=limits.fetch_batch_size,
                )
                if not raw_rows:
                    return GuardedSelectResult(
                        status="empty",
                        rows_seen=0,
                        encoded_bytes=0,
                    )
                records = await _load_canonical_records(cursor, case_id=case_id, rows=raw_rows)
                evidence = _validate_provenance(raw_rows, case_id=case_id, records=records)
                return GuardedSelectResult(
                    status="ok",
                    rows=evidence,
                    rows_seen=len(raw_rows),
                    encoded_bytes=encoded_bytes,
                    truncated=truncated,
                    diagnostic=(
                        SafeDiagnostic(
                            diagnostic_class=DiagnosticClass.RESOURCE_LIMIT,
                            code="result_truncated",
                            correctable=False,
                        )
                        if truncated
                        else None
                    ),
                )


async def _read_bounded_rows(
    cursor: AsyncCursor,
    *,
    max_rows: int,
    max_bytes: int,
    batch_size: int,
) -> tuple[list[Mapping[str, object]], int, bool]:
    rows: list[Mapping[str, object]] = []
    encoded_bytes = 0
    truncated = False
    while True:
        batch = await cursor.fetchmany(batch_size)
        if not batch:
            break
        for row in batch:
            if len(rows) >= max_rows:
                truncated = True
                return rows, encoded_bytes, truncated
            encoded = json.dumps(dict(row), sort_keys=True, separators=(",", ":"), default=str)
            row_bytes = len(encoded.encode("utf-8"))
            if encoded_bytes + row_bytes > max_bytes:
                truncated = True
                return rows, encoded_bytes, truncated
            rows.append(row)
            encoded_bytes += row_bytes
    return rows, encoded_bytes, truncated


async def _load_canonical_records(
    cursor: AsyncCursor,
    *,
    case_id: str,
    rows: Sequence[Mapping[str, object]],
) -> dict[str, _CanonicalRecord]:
    record_ids: set[str] = set()
    for row in rows:
        refs = _parse_source_refs(row.get("source_refs"))
        record_ids.update(ref.record_id for ref in refs)
    await cursor.execute(
        "SELECT record_id, case_id, content_hash, text, payload "
        "FROM public.records WHERE case_id = $1 AND record_id = ANY($2::text[])",
        (case_id, sorted(record_ids)),
    )
    records: dict[str, _CanonicalRecord] = {}
    for row in await cursor.fetchall():
        record_id = str(row["record_id"])
        payload = row.get("payload")
        records[record_id] = _CanonicalRecord(
            record_id=record_id,
            case_id=str(row["case_id"]),
            content_hash=str(row["content_hash"]),
            text=str(row["text"]) if row.get("text") is not None else None,
            payload=payload if isinstance(payload, Mapping) else {},
        )
    return records


def _validate_provenance(
    rows: Sequence[Mapping[str, object]],
    *,
    case_id: str,
    records: Mapping[str, _CanonicalRecord],
) -> tuple[StructuredRowEvidence, ...]:
    evidence: list[StructuredRowEvidence] = []
    for row in rows:
        row_case = row.get("case_id")
        record_id = row.get("record_id")
        content_hash = row.get("content_hash")
        if (
            row_case != case_id
            or not isinstance(record_id, str)
            or not isinstance(content_hash, str)
        ):
            raise ProvenanceValidationError("structured row has invalid trusted scope metadata")
        refs = _parse_source_refs(row.get("source_refs"))
        canonical = records.get(record_id)
        if canonical is None or canonical.content_hash != content_hash:
            raise ProvenanceValidationError("structured row content hash could not be resolved")
        if not refs or any(not _reference_resolves(ref, records) for ref in refs):
            raise ProvenanceValidationError(
                "structured row contains an unresolved source reference"
            )
        fields = tuple(
            ResultField(name=str(name), value=_bounded_scalar(value))
            for name, value in sorted(row.items())
            if name not in {"case_id", "content_hash", "source_refs"}
        )
        digest = hashlib.sha256(
            json.dumps(
                [(field.name, field.value) for field in fields],
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode()
        ).hexdigest()[:24]
        evidence.append(
            StructuredRowEvidence(
                evidence_id=f"row:{record_id}:{digest}",
                case_id=case_id,
                content_hash=content_hash,
                source_refs=refs,
                fields=fields,
            )
        )
    return tuple(evidence)


def _parse_source_refs(value: object) -> tuple[SourceRef, ...]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise ProvenanceValidationError("source references are not valid JSON") from error
    if not isinstance(value, list):
        raise ProvenanceValidationError("source references must be a JSON array")
    try:
        return tuple(SourceRef.model_validate(item) for item in value)
    except ValidationError as error:
        raise ProvenanceValidationError("source reference schema is invalid") from error


def _reference_resolves(ref: SourceRef, records: Mapping[str, _CanonicalRecord]) -> bool:
    record = records.get(ref.record_id)
    if record is None:
        return False
    locator = ref.locator
    if isinstance(locator, FieldLocator):
        return locator.field in {"text", "payload"} or locator.field in record.payload
    if not isinstance(locator, TextSpanLocator):
        return False
    if locator.field == "text":
        return record.text is not None and locator.matches(record.text)
    value = record.payload.get(locator.field)
    return isinstance(value, str) and locator.matches(value)


def _bounded_scalar(value: object) -> str | int | float | bool | None:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return encoded[:32_000]


class ProvenanceValidationError(ValueError):
    pass


def _remaining(deadline: float) -> float:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise TimeoutError("query deadline exhausted")
    return remaining


def _is_transient(error: Exception) -> bool:
    return isinstance(
        error,
        (
            psycopg.OperationalError,
            psycopg.InterfaceError,
            psycopg.errors.SerializationFailure,
            psycopg.errors.DeadlockDetected,
        ),
    )


def _safe_database_diagnostic(error: Exception) -> SafeDiagnostic:
    if isinstance(error, TimeoutError | psycopg.errors.QueryCanceled):
        return SafeDiagnostic(
            diagnostic_class=DiagnosticClass.RESOURCE_LIMIT,
            code="query_timeout",
            correctable=False,
        )
    if isinstance(error, psycopg.errors.UndefinedColumn | psycopg.errors.UndefinedTable):
        return SafeDiagnostic(
            diagnostic_class=DiagnosticClass.SCHEMA,
            code="schema_mismatch",
        )
    if isinstance(error, ProvenanceValidationError):
        return SafeDiagnostic(
            diagnostic_class=DiagnosticClass.EXECUTION,
            code="provenance_invalid",
            correctable=False,
        )
    if _is_transient(error):
        return SafeDiagnostic(
            diagnostic_class=DiagnosticClass.EXECUTION,
            code="transient_exhausted",
            correctable=False,
        )
    return SafeDiagnostic(
        diagnostic_class=DiagnosticClass.EXECUTION,
        code="execution_failed",
    )


__all__ = [
    "ExecutorLimits",
    "ProvenanceValidationError",
    "ReaderPool",
    "execute_guarded_select",
]
