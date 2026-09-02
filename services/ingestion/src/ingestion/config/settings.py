"""Ingestion settings: typed, validated before any I/O, with one YAML policy baseline.

Precedence: explicit kwargs, process environment, `.env`, `config/ingestion/<env>.yaml`,
class defaults. Everything with a safe default (pool, throttle, chunking, telemetry
endpoints and policy) is YAML policy that any environment variable of
the same name overrides for one process; the deployment contract (database, evidence,
region, model identifiers) is environment-only with no YAML or Python fallback. AWS credentials are never settings
because the SDK credential chain owns them.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import (
    AnyHttpUrl,
    AnyUrl,
    Field,
    PositiveInt,
    SecretStr,
    ValidationError,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

type LogExport = Literal["stdout", "otlp"]
type EnvironmentName = Literal["local"]
type LlmReasoningEffort = Literal["low", "medium", "high", "xhigh", "max"]

# Bumped whenever normalization, chunking, or extraction semantics change so that an
# existing store is rebuilt rather than trusted.
PIPELINE_VERSION = "ingestion@3"
CONFIG_DIR_ENV = "INGESTION_CONFIG_DIR"
ENVIRONMENT_ENV = "ENVIRONMENT_NAME"
_CONFIG_SUBDIR = Path("config") / "ingestion"


class SettingsError(RuntimeError):
    """Startup configuration is missing or invalid; the message names every field."""


def config_file(environment: str) -> Path | None:
    """Locate `config/ingestion/<environment>.yaml`.

    `INGESTION_CONFIG_DIR` is the operator escape hatch; otherwise the directory is found by
    walking upward from this module, which works for the editable source tree and for a
    non-editable install whose `config/` is mounted beside the virtual environment.
    """
    name = f"{environment}.yaml"
    override = os.environ.get(CONFIG_DIR_ENV)
    if override:
        return Path(override) / name
    for ancestor in Path(__file__).resolve().parents:
        candidate = ancestor / _CONFIG_SUBDIR / name
        if candidate.is_file():
            return candidate
    return None


# The only keys a YAML baseline may set. Everything else is deployment contract.
POLICY_FIELDS: frozenset[str] = frozenset(
    {
        "embedding_dimensions",
        "chunk_window_chars",
        "chunk_overlap_chars",
        "db_pool_size",
        "db_max_overflow",
        "db_pool_timeout_s",
        "db_pool_pre_ping",
        "llm_requests_per_minute",
        "llm_max_in_flight",
        "llm_max_retries",
        "llm_reasoning_effort",
        "otel_service_name",
        "capture_ai_content",
        "log_level",
        "otel_exporter_otlp_endpoint",
        "otel_exporter_otlp_traces_endpoint",
        "log_export",
        "service_version",
    }
)


class PolicyYamlSource(YamlConfigSettingsSource):
    """The YAML baseline restricted to application policy keys.

    A deployment value in YAML would be a stale fallback that makes a misconfigured
    deployment look valid, so it is rejected at startup rather than merged.
    """

    def __init__(self, settings_cls: type[BaseSettings], yaml_file: Path) -> None:
        super().__init__(settings_cls, yaml_file=yaml_file)
        data = self.yaml_data if isinstance(self.yaml_data, dict) else {}
        forbidden = sorted(set(data) - POLICY_FIELDS)
        if forbidden:
            raise SettingsError(
                f"{yaml_file} may only hold application policy; remove: {', '.join(forbidden)}"
            )


def _environment_name(*sources: PydanticBaseSettingsSource) -> str | None:
    """Resolve the environment selector from kwargs, process env, or `.env`, in that order."""
    for source in sources:
        values = source()
        value = values.get(ENVIRONMENT_ENV) or values.get("environment_name")
        if isinstance(value, str) and value:
            return value
    return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        populate_by_name=True,
        # Compose passes policy overrides through as empty strings when unset.
        env_ignore_empty=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        sources: list[PydanticBaseSettingsSource] = [init_settings, env_settings, dotenv_settings]
        # The selector is required and has no default: without it, no baseline is loaded
        # and validation fails naming ENVIRONMENT_NAME.
        environment = _environment_name(init_settings, env_settings, dotenv_settings)
        yaml_path = config_file(environment) if environment else None
        if yaml_path is not None:
            sources.append(PolicyYamlSource(settings_cls, yaml_path))
        return tuple(sources)

    environment_name: EnvironmentName = Field(
        alias=ENVIRONMENT_ENV, description="Selects config/ingestion/<name>.yaml; always required."
    )

    # Environment-only deployment contract.
    database_url: AnyUrl = Field(
        alias="DATABASE_URL", description="SQLAlchemy URL, postgresql+psycopg://..."
    )
    evidence_s3_endpoint: AnyHttpUrl = Field(
        alias="EVIDENCE_S3_ENDPOINT",
        description="S3-compatible endpoint that serves the evidence bucket.",
    )
    evidence_s3_bucket: str = Field(
        alias="EVIDENCE_S3_BUCKET",
        min_length=1,
        description="Deployment-owned bucket containing datasets and ingestion receipts.",
    )
    evidence_s3_access_key: SecretStr = Field(
        alias="EVIDENCE_S3_ACCESS_KEY",
        min_length=1,
        description="Bucket-scoped S3 access key.",
    )
    evidence_s3_secret_key: SecretStr = Field(
        alias="EVIDENCE_S3_SECRET_KEY",
        min_length=1,
        description="Bucket-scoped S3 secret key.",
    )
    dataset_edition: str = Field(
        alias="DATASET_EDITION",
        pattern=r"^[a-z0-9][a-z0-9_-]*$",
        description="Edition segment below datasets/ and indexes/ in the evidence bucket.",
    )
    aws_region: str = Field(alias="AWS_REGION", min_length=1, description="Bedrock region.")
    bedrock_chat_model_id: str = Field(
        alias="BEDROCK_CHAT_MODEL_ID", min_length=1, description="Chat model for extraction."
    )
    bedrock_embedding_model_id: str = Field(
        alias="BEDROCK_EMBEDDING_MODEL_ID", min_length=1, description="Embedding model."
    )
    # Rare runtime-only knobs.
    service_instance_id: str | None = Field(
        default=None,
        alias="SERVICE_INSTANCE_ID",
        description="Resource identity; the hostname is used when unset.",
    )

    # Application-owned policy, baselined in config/ingestion/<environment>.yaml.
    otel_exporter_otlp_endpoint: AnyHttpUrl | None = Field(
        default=None,
        alias="OTEL_EXPORTER_OTLP_ENDPOINT",
        description="Operational OTLP/HTTP base for metrics and logs; unset disables export.",
    )
    otel_exporter_otlp_traces_endpoint: AnyHttpUrl | None = Field(
        default=None,
        alias="OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        description="Full traces URL; defaults to <endpoint>/v1/traces when unset.",
    )
    log_export: LogExport = Field(default="stdout", alias="LOG_EXPORT")
    service_version: str = Field(default="dev", alias="SERVICE_VERSION")
    embedding_dimensions: PositiveInt = Field(default=1024, alias="EMBEDDING_DIMENSIONS")
    chunk_window_chars: PositiveInt = Field(default=4000, alias="CHUNK_WINDOW_CHARS")
    chunk_overlap_chars: PositiveInt = Field(default=200, alias="CHUNK_OVERLAP_CHARS")
    db_pool_size: PositiveInt = Field(default=5, alias="DB_POOL_SIZE")
    db_max_overflow: PositiveInt = Field(default=10, alias="DB_MAX_OVERFLOW")
    db_pool_timeout_s: PositiveInt = Field(default=30, alias="DB_POOL_TIMEOUT_S")
    db_pool_pre_ping: bool = Field(default=True, alias="DB_POOL_PRE_PING")
    llm_requests_per_minute: PositiveInt = Field(default=60, alias="LLM_REQUESTS_PER_MINUTE")
    llm_max_in_flight: PositiveInt = Field(default=60, alias="LLM_MAX_IN_FLIGHT")
    llm_max_retries: PositiveInt = Field(default=3, alias="LLM_MAX_RETRIES")
    llm_reasoning_effort: LlmReasoningEffort = Field(
        default="low",
        alias="LLM_REASONING_EFFORT",
        description="Reasoning effort used by chat models that support configurable reasoning.",
    )
    otel_service_name: str = Field(default="ingestion", alias="OTEL_SERVICE_NAME")
    capture_ai_content: bool = Field(default=True, alias="CAPTURE_AI_CONTENT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @model_validator(mode="after")
    def _otlp_logs_need_an_endpoint(self) -> Settings:
        if self.log_export == "otlp" and self.otel_exporter_otlp_endpoint is None:
            raise ValueError("LOG_EXPORT=otlp requires OTEL_EXPORTER_OTLP_ENDPOINT")
        if self.chunk_overlap_chars >= self.chunk_window_chars:
            raise ValueError("CHUNK_OVERLAP_CHARS must be smaller than CHUNK_WINDOW_CHARS")
        return self

    @property
    def metrics_endpoint(self) -> str | None:
        base = self.otel_exporter_otlp_endpoint
        return f"{str(base).rstrip('/')}/v1/metrics" if base else None

    @property
    def logs_endpoint(self) -> str | None:
        base = self.otel_exporter_otlp_endpoint
        return f"{str(base).rstrip('/')}/v1/logs" if base else None

    @property
    def traces_endpoint(self) -> str | None:
        if self.otel_exporter_otlp_traces_endpoint:
            return str(self.otel_exporter_otlp_traces_endpoint)
        base = self.otel_exporter_otlp_endpoint
        return f"{str(base).rstrip('/')}/v1/traces" if base else None


def load_settings() -> Settings:
    """Read the environment and YAML baseline once; raise `SettingsError` naming every problem."""
    try:
        return Settings()
    except ValidationError as exc:
        problems = ", ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise SettingsError(f"invalid ingestion configuration: {problems}") from exc
