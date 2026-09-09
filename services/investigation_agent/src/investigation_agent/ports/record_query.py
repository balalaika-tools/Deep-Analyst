"""Contracts for bounded, policy-gated structured-record queries."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal, Protocol

from evidence_model import SourceRef
from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, field_validator, model_validator

ALLOWLIST_VERSION = "agent_read@1"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ParameterType(StrEnum):
    TEXT = "text"
    INTEGER = "integer"
    NUMERIC = "numeric"
    BOOLEAN = "boolean"
    DATE = "date"
    TIMESTAMPTZ = "timestamptz"
    TEXT_ARRAY = "text_array"


type ParameterValue = str | int | Decimal | bool | date | datetime | tuple[str, ...] | None
type BoundParameterValue = str | int | Decimal | bool | date | datetime | list[str] | None


class SqlParameter(_StrictModel):
    position: Annotated[int, Field(ge=1, le=64)]
    parameter_type: ParameterType
    value: ParameterValue

    @model_validator(mode="before")
    @classmethod
    def _decode_json_parameter(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        decoded = dict(value)
        declared = decoded.get("parameter_type")
        raw = decoded.get("value")
        try:
            if declared == ParameterType.DATE and isinstance(raw, str):
                decoded["value"] = date.fromisoformat(raw)
            elif declared == ParameterType.TIMESTAMPTZ and isinstance(raw, str):
                decoded["value"] = datetime.fromisoformat(raw)
            elif declared == ParameterType.NUMERIC and isinstance(raw, str | float):
                decoded["value"] = Decimal(str(raw))
            elif declared == ParameterType.TEXT_ARRAY and isinstance(raw, list):
                decoded["value"] = tuple(raw)
        except (ValueError, TypeError) as error:
            raise ValueError("SQL parameter value is not valid for its declared type") from error
        return decoded

    @model_validator(mode="after")
    def _value_matches_declared_type(self) -> SqlParameter:
        value = self.value
        accepted: dict[ParameterType, tuple[type[Any], ...]] = {
            ParameterType.TEXT: (str,),
            ParameterType.INTEGER: (int,),
            ParameterType.NUMERIC: (int, Decimal),
            ParameterType.BOOLEAN: (bool,),
            ParameterType.DATE: (date,),
            ParameterType.TIMESTAMPTZ: (datetime,),
            ParameterType.TEXT_ARRAY: (tuple,),
        }
        if value is None:
            return self
        if self.parameter_type is ParameterType.INTEGER and isinstance(value, bool):
            raise ValueError("boolean is not an integer SQL parameter")
        if not isinstance(value, accepted[self.parameter_type]):
            raise ValueError("SQL parameter value does not match its declared type")
        if self.parameter_type is ParameterType.DATE and isinstance(value, datetime):
            raise ValueError("datetime is not a date SQL parameter")
        if self.parameter_type is ParameterType.TIMESTAMPTZ:
            assert isinstance(value, datetime)
            if value.tzinfo is None:
                raise ValueError("timestamptz parameters must be timezone-aware")
        if self.parameter_type is ParameterType.TEXT_ARRAY:
            assert isinstance(value, tuple)
            if not all(isinstance(item, str) for item in value):
                raise ValueError("text_array parameters may contain only strings")
        return self


class SqlPlan(_StrictModel):
    schema_version: Literal[1] = 1
    sql: Annotated[str, Field(min_length=1, max_length=20_000)]
    parameters: Annotated[tuple[SqlParameter, ...], Field(max_length=64)] = ()
    expected_shape: Annotated[str, Field(min_length=1, max_length=1_000)] = "rows"

    @model_validator(mode="after")
    def _parameter_positions_are_contiguous(self) -> SqlPlan:
        positions = [item.position for item in self.parameters]
        if positions != list(range(1, len(positions) + 1)):
            raise ValueError("SQL parameters must be ordered in contiguous one-based positions")
        return self

    def parameter_values(self) -> tuple[BoundParameterValue, ...]:
        return tuple(
            list(item.value) if isinstance(item.value, tuple) else item.value
            for item in self.parameters
        )


class DiagnosticClass(StrEnum):
    PARSE = "parse"
    POLICY = "policy"
    SCHEMA = "schema"
    EXECUTION = "execution"
    EMPTY = "empty"
    SEMANTIC = "semantic_insufficiency"
    RESOURCE_LIMIT = "resource_limit"
    CANCELLED = "cancelled"


class SafeDiagnostic(_StrictModel):
    diagnostic_class: DiagnosticClass
    code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    detail: Annotated[str | None, Field(max_length=256)] = None
    correctable: bool = True


class SchemaColumn(_StrictModel):
    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    data_type: Annotated[str, Field(min_length=1, max_length=64)]


class SchemaView(_StrictModel):
    name: Annotated[str, Field(pattern=r"^agent_read\.[a-z][a-z0-9_]{0,63}$")]
    columns: Annotated[tuple[SchemaColumn, ...], Field(min_length=1, max_length=64)]


class ServerSchemaDescription(_StrictModel):
    allowlist_version: Literal["agent_read@1"] = "agent_read@1"
    views: Annotated[tuple[SchemaView, ...], Field(min_length=1, max_length=16)]
    allowed_functions: Annotated[tuple[str, ...], Field(max_length=32)]
    allowed_operators: Annotated[tuple[str, ...], Field(max_length=32)]


class ResultField(_StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    value: str | int | float | bool | None

    @field_validator("value", mode="before")
    @classmethod
    def _bound_non_json_scalars(cls, value: Any) -> Any:
        if isinstance(value, Decimal | date | datetime):
            return str(value)
        if isinstance(value, bytes):
            return base64.b64encode(value).decode("ascii")
        return value


class StructuredRowEvidence(_StrictModel):
    evidence_id: Annotated[str, Field(min_length=1, max_length=256)]
    content_hash: Annotated[str, Field(pattern=_SHA256_PATTERN)]
    source_refs: Annotated[tuple[SourceRef, ...], Field(min_length=1, max_length=32)]
    kind: Literal["row"] = "row"
    content: None = None
    evidentiary_status: Literal["verified"] = "verified"
    fields: Annotated[tuple[ResultField, ...], Field(max_length=64)] = ()
    provenance: Literal["structured"] = "structured"


class GuardedSelectResult(_StrictModel):
    status: Literal["ok", "empty", "rejected", "failed", "cancelled"]
    rows: Annotated[tuple[StructuredRowEvidence, ...], Field(max_length=1_000)] = ()
    diagnostic: SafeDiagnostic | None = None
    physical_attempts: Annotated[int, Field(ge=0, le=12)] = 0
    rows_seen: NonNegativeInt = 0
    encoded_bytes: NonNegativeInt = 0
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class ValidatedSelect:
    canonical_sql: str
    parameter_count: int
    referenced_views: tuple[str, ...]
    fingerprint: str


class SqlPolicyViolation(ValueError):
    def __init__(self, code: str, detail: str, *, diagnostic_class: DiagnosticClass) -> None:
        super().__init__(detail)
        self.diagnostic = SafeDiagnostic(
            diagnostic_class=diagnostic_class,
            code=code,
            detail=detail,
        )


class RecordQueryExecutor(Protocol):
    def schema_description(self) -> ServerSchemaDescription: ...

    def validate(self, plan: SqlPlan) -> ValidatedSelect: ...

    async def execute(self, plan: SqlPlan, *, deadline: float) -> GuardedSelectResult: ...


def digest_payload(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


__all__ = [
    "ALLOWLIST_VERSION",
    "DiagnosticClass",
    "GuardedSelectResult",
    "ParameterType",
    "RecordQueryExecutor",
    "ResultField",
    "SafeDiagnostic",
    "SchemaColumn",
    "SchemaView",
    "ServerSchemaDescription",
    "SqlParameter",
    "SqlPlan",
    "SqlPolicyViolation",
    "StructuredRowEvidence",
    "ValidatedSelect",
    "digest_payload",
]
