"""Provider failure classification shared by every Bedrock-backed capability."""

from __future__ import annotations

from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectionError,
    EndpointConnectionError,
    NoCredentialsError,
    ReadTimeoutError,
)

from ingestion.ports.entity_extractor import (
    ExtractionError,
    PermanentExtractionError,
    TransientExtractionError,
)

TRANSIENT_ERROR_CODES: frozenset[str] = frozenset(
    {
        "ThrottlingException",
        "TooManyRequestsException",
        "ServiceUnavailableException",
        "InternalServerException",
        "ModelTimeoutException",
        "ModelNotReadyException",
        "ServiceQuotaExceededException",
    }
)
_TRANSIENT_TYPES: tuple[type[BaseException], ...] = (
    TimeoutError,
    ConnectionError,
    EndpointConnectionError,
    ReadTimeoutError,
)


def error_code(exc: BaseException) -> str | None:
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code")
        return str(code) if code else None
    return None


def is_transient(exc: BaseException) -> bool:
    """True for throttling, timeouts, and connection failures worth another attempt."""
    if isinstance(exc, TransientExtractionError):
        return True
    if isinstance(exc, ClientError):
        return error_code(exc) in TRANSIENT_ERROR_CODES
    return isinstance(exc, _TRANSIENT_TYPES)


def translate_provider_error(exc: BaseException, *, operation: str) -> ExtractionError:
    """Map SDK and framework failures onto the port taxonomy; the cause is chained."""
    if isinstance(exc, ExtractionError):
        return exc
    code = error_code(exc) or type(exc).__name__
    message = f"{operation} failed with {code}"
    if is_transient(exc):
        return TransientExtractionError(message)
    if isinstance(exc, NoCredentialsError | ClientError | BotoCoreError):
        return PermanentExtractionError(message)
    return PermanentExtractionError(message)
