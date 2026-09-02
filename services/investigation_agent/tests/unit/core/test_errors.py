import pytest
from investigation_agent.core.errors import (
    AdapterBudgetExhaustedError,
    AdapterCancelledError,
    AdapterConflictError,
    AdapterDependencyUnavailableError,
    AdapterIncompatibleStateError,
    AdapterNoSupportError,
    AdapterPolicyRejectedError,
    AdapterTransientExhaustedError,
    AdapterValidationError,
    BudgetExhaustedFailure,
    CancelledFailure,
    ConflictFailure,
    DependencyUnavailableFailure,
    IncompatibleStateFailure,
    InternalFailure,
    InvestigationFailure,
    NoSupportFailure,
    PolicyRejectedFailure,
    TransientExhaustedFailure,
    ValidationFailure,
    translate_adapter_error,
)

FAILURE_TYPES = (
    ValidationFailure,
    ConflictFailure,
    PolicyRejectedFailure,
    NoSupportFailure,
    TransientExhaustedFailure,
    BudgetExhaustedFailure,
    CancelledFailure,
    DependencyUnavailableFailure,
    IncompatibleStateFailure,
    InternalFailure,
)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(AdapterValidationError, ValidationFailure, id="validation"),
        pytest.param(AdapterConflictError, ConflictFailure, id="conflict"),
        pytest.param(AdapterPolicyRejectedError, PolicyRejectedFailure, id="policy"),
        pytest.param(AdapterNoSupportError, NoSupportFailure, id="no-support"),
        pytest.param(
            AdapterTransientExhaustedError,
            TransientExhaustedFailure,
            id="transient-exhausted",
        ),
        pytest.param(AdapterBudgetExhaustedError, BudgetExhaustedFailure, id="budget"),
        pytest.param(AdapterCancelledError, CancelledFailure, id="cancelled"),
        pytest.param(
            AdapterDependencyUnavailableError,
            DependencyUnavailableFailure,
            id="dependency",
        ),
        pytest.param(
            AdapterIncompatibleStateError,
            IncompatibleStateFailure,
            id="incompatible-state",
        ),
        pytest.param(RuntimeError, InternalFailure, id="internal"),
    ],
)
def test_every_boundary_exception_maps_to_exactly_one_safe_failure(
    source: type[BaseException],
    expected: type[InvestigationFailure],
) -> None:
    sentinel = "private-source-detail"

    failure = translate_adapter_error(source(sentinel))

    assert type(failure) is expected
    assert sum(type(failure) is candidate for candidate in FAILURE_TYPES) == 1
    assert sentinel not in str(failure)
    assert failure.code and failure.public_message


def test_already_translated_failure_is_not_wrapped_again() -> None:
    original = DependencyUnavailableFailure()

    assert translate_adapter_error(original) is original
