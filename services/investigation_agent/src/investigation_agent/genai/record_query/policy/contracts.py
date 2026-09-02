"""Validated-plan and policy-failure contracts."""

from dataclasses import dataclass

from investigation_agent.genai.record_query.schemas import DiagnosticClass, SafeDiagnostic


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


__all__ = ["SqlPolicyViolation", "ValidatedSelect"]
