"""Stable failure taxonomy and one-way adapter error translation."""

from __future__ import annotations

import asyncio
from enum import StrEnum

import psycopg


class FailureKind(StrEnum):
    VALIDATION = "validation"
    CONFLICT = "conflict"
    POLICY_REJECTED = "policy_rejected"
    NO_SUPPORT = "no_support"
    TRANSIENT_EXHAUSTED = "transient_exhausted"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CANCELLED = "cancelled"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    INCOMPATIBLE_STATE = "incompatible_state"
    INTERNAL = "internal"


class InvestigationFailure(RuntimeError):
    """A bounded failure safe to expose through problem details or SSE."""

    kind: FailureKind
    code: str
    public_message: str
    retryable: bool = False

    def __init__(self) -> None:
        super().__init__(self.public_message)


class ValidationFailure(InvestigationFailure):
    kind = FailureKind.VALIDATION
    code = "invalid_request"
    public_message = "The request or configuration is invalid."


class ConflictFailure(InvestigationFailure):
    kind = FailureKind.CONFLICT
    code = "conflict"
    public_message = "The request conflicts with the current resource state."


class PolicyRejectedFailure(InvestigationFailure):
    kind = FailureKind.POLICY_REJECTED
    code = "policy_rejected"
    public_message = "The requested operation is not permitted by policy."


class NoSupportFailure(InvestigationFailure):
    kind = FailureKind.NO_SUPPORT
    code = "no_support"
    public_message = "No supporting evidence was retrieved within the configured limits."


class TransientExhaustedFailure(InvestigationFailure):
    kind = FailureKind.TRANSIENT_EXHAUSTED
    code = "transient_exhausted"
    public_message = "Temporary operation attempts were exhausted."
    retryable = True


class BudgetExhaustedFailure(InvestigationFailure):
    kind = FailureKind.BUDGET_EXHAUSTED
    code = "budget_exhausted"
    public_message = "The investigation reached a configured execution limit."


class CancelledFailure(InvestigationFailure):
    kind = FailureKind.CANCELLED
    code = "cancelled"
    public_message = "The investigation was cancelled."


class DependencyUnavailableFailure(InvestigationFailure):
    kind = FailureKind.DEPENDENCY_UNAVAILABLE
    code = "dependency_unavailable"
    public_message = "A required service is temporarily unavailable."
    retryable = True


class IncompatibleStateFailure(InvestigationFailure):
    kind = FailureKind.INCOMPATIBLE_STATE
    code = "incompatible_state"
    public_message = "The saved investigation state is not supported by this service version."


class InternalFailure(InvestigationFailure):
    kind = FailureKind.INTERNAL
    code = "internal"
    public_message = "The investigation could not be completed."


class AdapterFailure(RuntimeError):
    """Base for sanitized adapter failures translated at the application boundary."""


class AdapterValidationError(AdapterFailure):
    pass


class AdapterConflictError(AdapterFailure):
    pass


class AdapterPolicyRejectedError(AdapterFailure):
    pass


class AdapterNoSupportError(AdapterFailure):
    pass


class AdapterTransientExhaustedError(AdapterFailure):
    pass


class AdapterBudgetExhaustedError(AdapterFailure):
    pass


class AdapterCancelledError(AdapterFailure):
    pass


class AdapterDependencyUnavailableError(AdapterFailure):
    pass


class AdapterIncompatibleStateError(AdapterFailure):
    pass


_TRANSLATIONS: tuple[tuple[type[BaseException], type[InvestigationFailure]], ...] = (
    (AdapterValidationError, ValidationFailure),
    (AdapterConflictError, ConflictFailure),
    (AdapterPolicyRejectedError, PolicyRejectedFailure),
    (AdapterNoSupportError, NoSupportFailure),
    (AdapterTransientExhaustedError, TransientExhaustedFailure),
    (AdapterBudgetExhaustedError, BudgetExhaustedFailure),
    (AdapterCancelledError, CancelledFailure),
    (asyncio.CancelledError, CancelledFailure),
    (AdapterDependencyUnavailableError, DependencyUnavailableFailure),
    (AdapterIncompatibleStateError, IncompatibleStateFailure),
    # Connection-level psycopg errors (psycopg_pool's PoolTimeout/PoolClosed subclass
    # OperationalError) mean the database is unreachable or overloaded, which is retryable.
    (psycopg.OperationalError, DependencyUnavailableFailure),
    (psycopg.InterfaceError, DependencyUnavailableFailure),
)

_CODE_TRANSLATIONS: dict[str, type[InvestigationFailure]] = {
    "thread_full": ConflictFailure,
    "grounding_failed": NoSupportFailure,
    "guardrail_unavailable": DependencyUnavailableFailure,
    "request_in_progress": ConflictFailure,
    "policy_rejected": PolicyRejectedFailure,
    "no_support": NoSupportFailure,
    "no_retrieved_support": NoSupportFailure,
    "retrieval_incomplete": NoSupportFailure,
    "transient_exhausted": TransientExhaustedFailure,
    "budget_exhausted": BudgetExhaustedFailure,
    "cancelled": CancelledFailure,
    "dependency_unavailable": DependencyUnavailableFailure,
    "incompatible_state": IncompatibleStateFailure,
}


def translate_adapter_error(error: BaseException) -> InvestigationFailure:
    """Translate once to a safe failure without copying source exception text."""

    if isinstance(error, InvestigationFailure):
        return error
    for source_type, failure_type in _TRANSLATIONS:
        if isinstance(error, source_type):
            return failure_type()
    code = getattr(error, "code", None)
    code_failure_type = _CODE_TRANSLATIONS.get(code) if isinstance(code, str) else None
    return code_failure_type() if code_failure_type else InternalFailure()


__all__ = [
    "AdapterBudgetExhaustedError",
    "AdapterCancelledError",
    "AdapterConflictError",
    "AdapterDependencyUnavailableError",
    "AdapterFailure",
    "AdapterIncompatibleStateError",
    "AdapterNoSupportError",
    "AdapterPolicyRejectedError",
    "AdapterTransientExhaustedError",
    "AdapterValidationError",
    "BudgetExhaustedFailure",
    "CancelledFailure",
    "ConflictFailure",
    "DependencyUnavailableFailure",
    "FailureKind",
    "IncompatibleStateFailure",
    "InternalFailure",
    "InvestigationFailure",
    "NoSupportFailure",
    "PolicyRejectedFailure",
    "TransientExhaustedFailure",
    "ValidationFailure",
    "translate_adapter_error",
]
