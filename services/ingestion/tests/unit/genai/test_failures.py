from botocore.exceptions import ClientError, NoCredentialsError
from ingestion.genai.shared.failures import is_retryable, is_transient, translate_provider_error
from ingestion.ports.entity_extractor import PermanentExtractionError, TransientExtractionError


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "x"}}, "Converse")


def test_throttling_and_timeouts_are_transient() -> None:
    assert is_transient(_client_error("ThrottlingException"))
    assert is_transient(TimeoutError())
    assert isinstance(
        translate_provider_error(_client_error("ThrottlingException"), operation="chat"),
        TransientExtractionError,
    )


def test_expired_or_missing_credentials_are_permanent() -> None:
    for exc in (
        _client_error("ExpiredTokenException"),
        _client_error("AccessDeniedException"),
        NoCredentialsError(),
    ):
        assert not is_transient(exc)
        translated = translate_provider_error(exc, operation="chat")
        assert isinstance(translated, PermanentExtractionError)
    assert "ExpiredTokenException" in str(
        translate_provider_error(_client_error("ExpiredTokenException"), operation="chat")
    )


def test_permanent_provider_failures_are_not_retryable() -> None:
    assert not is_retryable(_client_error("ExpiredTokenException"))
    assert not is_retryable(NoCredentialsError())
