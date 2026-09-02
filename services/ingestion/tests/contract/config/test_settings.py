from pathlib import Path

import pytest
from ingestion.config.settings import (
    CONFIG_DIR_ENV,
    POLICY_FIELDS,
    Settings,
    SettingsError,
    config_file,
    load_settings,
)

REPO_ROOT = Path(__file__).resolve().parents[5]

COMPLETE_ENV = {
    "ENVIRONMENT_NAME": "local",
    "DATABASE_URL": "postgresql+psycopg://app:pw@127.0.0.1:5432/app",
    "EVIDENCE_S3_ENDPOINT": "http://127.0.0.1:9090",
    "EVIDENCE_S3_BUCKET": "evidence-test",
    "EVIDENCE_S3_ACCESS_KEY": "evidence-user",
    "EVIDENCE_S3_SECRET_KEY": "evidence-secret",
    "DATASET_EDITION": "en",
    "AWS_REGION": "eu-central-1",
    "BEDROCK_CHAT_MODEL_ID": "example.chat-model-v1:0",
    "BEDROCK_EMBEDDING_MODEL_ID": "amazon.titan-embed-text-v2:0",
}


@pytest.fixture
def complete_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in COMPLETE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setitem(Settings.model_config, "env_file", None)


def test_defaults_match_the_documented_contract(complete_env: None) -> None:
    settings = load_settings()

    assert (settings.db_pool_size, settings.db_max_overflow, settings.db_pool_timeout_s) == (
        5,
        10,
        30,
    )
    assert settings.db_pool_pre_ping is True
    assert (settings.llm_requests_per_minute, settings.llm_max_in_flight) == (60, 60)
    assert settings.llm_reasoning_effort == "low"
    assert settings.embedding_dimensions == 1024
    assert settings.capture_ai_content is True
    assert settings.otel_service_name == "ingestion"
    assert settings.log_export == "otlp", "the committed baseline delivers logs over OTLP"
    assert settings.traces_endpoint == "http://lgtm:4328/v1/traces"
    assert settings.metrics_endpoint == "http://lgtm:4318/v1/metrics"


def test_missing_environment_name_fails_before_any_baseline_is_read(
    complete_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ENVIRONMENT_NAME")

    with pytest.raises(SettingsError, match="ENVIRONMENT_NAME"):
        load_settings()


def test_missing_chat_model_id_fails_naming_the_setting(
    complete_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BEDROCK_CHAT_MODEL_ID")

    with pytest.raises(SettingsError, match="BEDROCK_CHAT_MODEL_ID"):
        load_settings()


def test_missing_bucket_secret_fails_naming_the_setting(
    complete_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("EVIDENCE_S3_SECRET_KEY")

    with pytest.raises(SettingsError, match="EVIDENCE_S3_SECRET_KEY"):
        load_settings()


def test_zero_in_flight_limit_fails_naming_the_setting(
    complete_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_MAX_IN_FLIGHT", "0")

    with pytest.raises(SettingsError, match="LLM_MAX_IN_FLIGHT"):
        load_settings()


def test_physical_request_contract_has_one_trace_override_and_no_embedding_batch_setting(
    complete_env: None,
) -> None:
    aliases = {str(field.alias) for field in Settings.model_fields.values()}

    assert {name for name in aliases if name.endswith("_TRACES_ENDPOINT")} == {
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"
    }
    assert "GENAI_OTLP_TRACES_ENDPOINT" not in aliases
    assert "EMBEDDING_BATCH_SIZE" not in aliases
    assert "embedding_batch_size" not in POLICY_FIELDS
    assert not hasattr(load_settings(), "embedding_batch_size")


def test_invalid_reasoning_effort_fails_naming_the_setting(
    complete_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_REASONING_EFFORT", "minimal")

    with pytest.raises(SettingsError, match="LLM_REASONING_EFFORT"):
        load_settings()


def test_trace_endpoint_derives_from_base_unless_overridden(
    complete_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "local.yaml").write_text("otel_exporter_otlp_endpoint: http://collector:4318/\n")
    monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))
    assert load_settings().traces_endpoint == "http://collector:4318/v1/traces"
    assert load_settings().logs_endpoint == "http://collector:4318/v1/logs"

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://collector:4328/v1/traces")
    assert load_settings().traces_endpoint == "http://collector:4328/v1/traces"
    assert load_settings().metrics_endpoint == "http://collector:4318/v1/metrics"
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "")
    assert load_settings().traces_endpoint == "http://collector:4318/v1/traces", "empty is unset"


def test_yaml_baseline_is_found_and_environment_overrides_it(
    complete_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    baseline = config_file("local")
    assert baseline is not None and baseline.name == "local.yaml"
    assert baseline.parent == REPO_ROOT / "config" / "ingestion"

    (tmp_path / "local.yaml").write_text(
        "llm_max_in_flight: 7\nllm_reasoning_effort: high\nchunk_window_chars: 999\n"
    )
    monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))
    settings = load_settings()
    assert settings.llm_max_in_flight == 7 and settings.chunk_window_chars == 999
    assert settings.llm_reasoning_effort == "high"
    assert settings.db_pool_size == 5, "class default when the baseline omits a key"

    monkeypatch.setenv("LLM_MAX_IN_FLIGHT", "3")
    monkeypatch.setenv("LLM_REASONING_EFFORT", "medium")
    assert load_settings().llm_max_in_flight == 3, "process environment wins over YAML"
    assert load_settings().llm_reasoning_effort == "medium"


def test_yaml_baseline_never_supplies_the_deployment_contract(
    complete_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "local.yaml").write_text("bedrock_chat_model_id: from-yaml\n")
    monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))
    with pytest.raises(SettingsError, match="bedrock_chat_model_id"):
        load_settings()


def test_committed_baseline_holds_only_application_policy() -> None:
    import yaml

    keys = set(yaml.safe_load((REPO_ROOT / "config" / "ingestion" / "local.yaml").read_text()))
    deployment_only = {
        "database_url",
        "evidence_s3_endpoint",
        "evidence_s3_bucket",
        "evidence_s3_access_key",
        "evidence_s3_secret_key",
        "dataset_edition",
        "aws_region",
        "bedrock_chat_model_id",
        "bedrock_embedding_model_id",
    }
    assert keys.isdisjoint(deployment_only)
    assert keys == POLICY_FIELDS
    assert POLICY_FIELDS < set(Settings.model_fields)
