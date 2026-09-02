"""Typed, layered non-secret configuration for the investigation service.

Precedence is explicit constructor input, process environment, ``.env``,
``config/investigation-agent/<environment>.yaml``, then typed code defaults. The YAML source is
restricted to the allowlisted application policy in :data:`POLICY_FIELDS`; deployment coordinates
and credentials cannot acquire a stale committed fallback. The prototype has no authentication
or authorization inputs.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Literal, Self

from pydantic import AnyHttpUrl, Field, ValidationError, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

type EnvironmentName = Literal["local"]
type LogExport = Literal["stdout", "otlp"]
type LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
type ModelReasoningEffort = Literal["low", "medium", "high", "xhigh", "max"]

CONFIG_DIR_ENV = "INVESTIGATION_AGENT_CONFIG_DIR"
ENVIRONMENT_ENV = "ENVIRONMENT_NAME"
_CONFIG_SUBDIR = Path("config") / "investigation-agent"


class SettingsError(RuntimeError):
    """Startup configuration is missing or invalid; values are never included."""


def config_file(environment: str) -> Path | None:
    """Locate the selected policy baseline in source trees and installed layouts."""

    name = f"{environment}.yaml"
    override = os.environ.get(CONFIG_DIR_ENV)
    if override:
        return Path(override) / name
    for ancestor in Path(__file__).resolve().parents:
        candidate = ancestor / _CONFIG_SUBDIR / name
        if candidate.is_file():
            return candidate
    return None


POLICY_FIELDS: frozenset[str] = frozenset(
    {
        "reader_pool_min_size",
        "reader_pool_max_size",
        "writer_pool_min_size",
        "writer_pool_max_size",
        "pool_acquire_timeout_s",
        "readiness_timeout_s",
        "shutdown_timeout_s",
        "turn_timeout_s",
        "model_timeout_s",
        "tool_timeout_s",
        "model_retry_attempts",
        "tool_retry_attempts",
        "retry_initial_delay_s",
        "retry_backoff_factor",
        "retry_max_delay_s",
        "retry_jitter",
        "main_model_call_limit",
        "main_tool_call_limit",
        "nested_model_call_limit",
        "nested_tool_call_limit",
        "closure_model_calls",
        "max_physical_attempts",
        "max_evidence_cards",
        "max_retrieved_rows",
        "max_query_rows",
        "max_result_bytes",
        "max_graph_paths",
        "max_context_tokens",
        "max_answer_chars",
        "lexical_weight",
        "vector_weight",
        "reciprocal_rank_constant",
        "retrieval_top_k",
        "graph_max_depth",
        "graph_max_nodes",
        "graph_max_edges",
        "max_history_turns",
        "history_page_size",
        "history_max_page_size",
        "sse_heartbeat_s",
        "sse_chunk_chars",
        "model_temperature",
        "model_reasoning_effort",
        "otel_service_name",
        "service_version",
        "otel_exporter_otlp_endpoint",
        "otel_exporter_otlp_traces_endpoint",
        "log_export",
        "capture_ai_content",
        "log_level",
    }
)


class PolicyYamlSource(YamlConfigSettingsSource):
    """Fail closed when committed YAML contains a deployment field or a secret."""

    def __init__(self, settings_cls: type[BaseSettings], yaml_file: Path) -> None:
        super().__init__(settings_cls, yaml_file=yaml_file)
        data = self.yaml_data if isinstance(self.yaml_data, dict) else {}
        forbidden = sorted(set(data) - POLICY_FIELDS)
        if forbidden:
            names = ", ".join(str(name) for name in forbidden)
            raise SettingsError(
                f"{yaml_file} may only hold application policy; remove fields: {names}"
            )


def _environment_name(*sources: PydanticBaseSettingsSource) -> str | None:
    for source in sources:
        values = source()
        value = values.get(ENVIRONMENT_ENV) or values.get("environment_name")
        if isinstance(value, str) and value:
            return value
    return None


class Settings(BaseSettings):
    """Flat non-secret service settings validated before any external resource is built."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        populate_by_name=True,
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
        del file_secret_settings
        sources: list[PydanticBaseSettingsSource] = [init_settings, env_settings, dotenv_settings]
        environment = _environment_name(init_settings, env_settings, dotenv_settings)
        yaml_path = config_file(environment) if environment else None
        if yaml_path is not None:
            sources.append(PolicyYamlSource(settings_cls, yaml_path))
        return tuple(sources)

    # Environment-only deployment contract.
    environment_name: EnvironmentName = Field(alias=ENVIRONMENT_ENV)
    investigation_agent_host: str = Field(
        alias="INVESTIGATION_AGENT_HOST", min_length=1, max_length=255
    )
    investigation_agent_port: int = Field(alias="INVESTIGATION_AGENT_PORT", ge=1, le=65_535)
    aws_region: str = Field(alias="AWS_REGION", min_length=1, max_length=64)
    bedrock_chat_model_id: str = Field(alias="BEDROCK_CHAT_MODEL_ID", min_length=1, max_length=512)
    bedrock_embedding_model_id: str = Field(
        alias="BEDROCK_EMBEDDING_MODEL_ID", min_length=1, max_length=512
    )
    expected_agent_initializer_version: str = Field(
        alias="EXPECTED_AGENT_INITIALIZER_VERSION",
        pattern=r"^[a-z0-9][a-z0-9_.@-]{0,63}$",
    )
    service_instance_id: str | None = Field(
        default=None, alias="SERVICE_INSTANCE_ID", min_length=1, max_length=128
    )

    # Non-secret application policy baselined in config/investigation-agent/<environment>.yaml.
    reader_pool_min_size: int = Field(default=1, alias="READER_POOL_MIN_SIZE", ge=0, le=64)
    reader_pool_max_size: int = Field(default=5, alias="READER_POOL_MAX_SIZE", ge=1, le=64)
    writer_pool_min_size: int = Field(default=1, alias="WRITER_POOL_MIN_SIZE", ge=0, le=64)
    writer_pool_max_size: int = Field(default=5, alias="WRITER_POOL_MAX_SIZE", ge=1, le=64)
    pool_acquire_timeout_s: float = Field(default=5.0, alias="POOL_ACQUIRE_TIMEOUT_S", gt=0, le=120)
    readiness_timeout_s: float = Field(default=5.0, alias="READINESS_TIMEOUT_S", gt=0, le=60)
    shutdown_timeout_s: float = Field(default=30.0, alias="SHUTDOWN_TIMEOUT_S", gt=0, le=300)
    turn_timeout_s: float = Field(default=120.0, alias="TURN_TIMEOUT_S", gt=0, le=900)
    model_timeout_s: float = Field(default=45.0, alias="MODEL_TIMEOUT_S", gt=0, le=300)
    tool_timeout_s: float = Field(default=30.0, alias="TOOL_TIMEOUT_S", gt=0, le=300)
    model_retry_attempts: int = Field(default=3, alias="MODEL_RETRY_ATTEMPTS", ge=1, le=10)
    tool_retry_attempts: int = Field(default=3, alias="TOOL_RETRY_ATTEMPTS", ge=1, le=10)
    retry_initial_delay_s: float = Field(default=0.25, alias="RETRY_INITIAL_DELAY_S", ge=0, le=30)
    retry_backoff_factor: float = Field(default=2.0, alias="RETRY_BACKOFF_FACTOR", ge=1, le=10)
    retry_max_delay_s: float = Field(default=4.0, alias="RETRY_MAX_DELAY_S", ge=0, le=120)
    retry_jitter: bool = Field(default=True, alias="RETRY_JITTER")
    main_model_call_limit: int = Field(default=20, alias="MAIN_MODEL_CALL_LIMIT", ge=2, le=100)
    main_tool_call_limit: int = Field(default=12, alias="MAIN_TOOL_CALL_LIMIT", ge=1, le=100)
    nested_model_call_limit: int = Field(default=6, alias="NESTED_MODEL_CALL_LIMIT", ge=1, le=20)
    nested_tool_call_limit: int = Field(default=3, alias="NESTED_TOOL_CALL_LIMIT", ge=1, le=3)
    closure_model_calls: int = Field(default=1, alias="CLOSURE_MODEL_CALLS", ge=1, le=10)
    max_physical_attempts: int = Field(default=40, alias="MAX_PHYSICAL_ATTEMPTS", ge=1, le=1_000)
    max_evidence_cards: int = Field(default=200, alias="MAX_EVIDENCE_CARDS", ge=8, le=5_000)
    max_retrieved_rows: int = Field(default=256, alias="MAX_RETRIEVED_ROWS", ge=1, le=10_000)
    max_query_rows: int = Field(default=100, alias="MAX_QUERY_ROWS", ge=1, le=10_000)
    max_result_bytes: int = Field(
        default=1_000_000, alias="MAX_RESULT_BYTES", ge=1_024, le=100_000_000
    )
    max_graph_paths: int = Field(default=50, alias="MAX_GRAPH_PATHS", ge=1, le=1_000)
    max_context_tokens: int = Field(
        default=32_000, alias="MAX_CONTEXT_TOKENS", ge=1_024, le=1_000_000
    )
    max_answer_chars: int = Field(default=16_000, alias="MAX_ANSWER_CHARS", ge=256, le=1_000_000)
    lexical_weight: float = Field(default=0.5, alias="LEXICAL_WEIGHT", ge=0, le=1)
    vector_weight: float = Field(default=0.5, alias="VECTOR_WEIGHT", ge=0, le=1)
    reciprocal_rank_constant: int = Field(
        default=60, alias="RECIPROCAL_RANK_CONSTANT", ge=1, le=10_000
    )
    retrieval_top_k: int = Field(default=20, alias="RETRIEVAL_TOP_K", ge=1, le=1_000)
    graph_max_depth: int = Field(default=4, alias="GRAPH_MAX_DEPTH", ge=1, le=16)
    graph_max_nodes: int = Field(default=100, alias="GRAPH_MAX_NODES", ge=1, le=10_000)
    graph_max_edges: int = Field(default=200, alias="GRAPH_MAX_EDGES", ge=1, le=20_000)
    max_history_turns: int = Field(default=50, alias="MAX_HISTORY_TURNS", ge=1, le=1_000)
    history_page_size: int = Field(default=20, alias="HISTORY_PAGE_SIZE", ge=1, le=1_000)
    history_max_page_size: int = Field(default=100, alias="HISTORY_MAX_PAGE_SIZE", ge=1, le=1_000)
    sse_heartbeat_s: float = Field(default=15.0, alias="SSE_HEARTBEAT_S", gt=0, le=120)
    sse_chunk_chars: int = Field(default=512, alias="SSE_CHUNK_CHARS", ge=1, le=16_384)
    model_temperature: float = Field(default=0.0, alias="MODEL_TEMPERATURE", ge=0, le=2)
    model_reasoning_effort: ModelReasoningEffort = Field(
        default="low", alias="MODEL_REASONING_EFFORT"
    )
    otel_service_name: str = Field(
        default="investigation-agent", alias="OTEL_SERVICE_NAME", min_length=1, max_length=128
    )
    service_version: str = Field(
        default="dev", alias="SERVICE_VERSION", min_length=1, max_length=64
    )
    otel_exporter_otlp_endpoint: AnyHttpUrl | None = Field(
        default=None, alias="OTEL_EXPORTER_OTLP_ENDPOINT"
    )
    otel_exporter_otlp_traces_endpoint: AnyHttpUrl | None = Field(
        default=None, alias="OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"
    )
    log_export: LogExport = Field(default="stdout", alias="LOG_EXPORT")
    capture_ai_content: bool = Field(default=False, alias="CAPTURE_AI_CONTENT")
    log_level: LogLevel = Field(default="INFO", alias="LOG_LEVEL")

    @model_validator(mode="after")
    def _validate_related_bounds(self) -> Self:
        if self.reader_pool_min_size > self.reader_pool_max_size:
            raise ValueError("READER_POOL_MIN_SIZE must not exceed READER_POOL_MAX_SIZE")
        if self.writer_pool_min_size > self.writer_pool_max_size:
            raise ValueError("WRITER_POOL_MIN_SIZE must not exceed WRITER_POOL_MAX_SIZE")
        if self.retry_initial_delay_s > self.retry_max_delay_s:
            raise ValueError("RETRY_INITIAL_DELAY_S must not exceed RETRY_MAX_DELAY_S")
        if self.model_timeout_s > self.turn_timeout_s or self.tool_timeout_s > self.turn_timeout_s:
            raise ValueError("MODEL_TIMEOUT_S and TOOL_TIMEOUT_S must not exceed TURN_TIMEOUT_S")
        if self.closure_model_calls >= self.main_model_call_limit:
            raise ValueError("CLOSURE_MODEL_CALLS must be smaller than MAIN_MODEL_CALL_LIMIT")
        if self.retrieval_top_k > self.max_retrieved_rows:
            raise ValueError("RETRIEVAL_TOP_K must not exceed MAX_RETRIEVED_ROWS")
        if self.history_page_size > self.history_max_page_size:
            raise ValueError("HISTORY_PAGE_SIZE must not exceed HISTORY_MAX_PAGE_SIZE")
        if self.sse_chunk_chars > self.max_answer_chars:
            raise ValueError("SSE_CHUNK_CHARS must not exceed MAX_ANSWER_CHARS")
        if not math.isclose(self.lexical_weight + self.vector_weight, 1.0, abs_tol=1e-9):
            raise ValueError("LEXICAL_WEIGHT and VECTOR_WEIGHT must sum to 1")
        if self.log_export == "otlp" and self.otel_exporter_otlp_endpoint is None:
            raise ValueError("LOG_EXPORT=otlp requires OTEL_EXPORTER_OTLP_ENDPOINT")
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
    """Load and validate all non-secret inputs while reporting field names only."""

    try:
        return Settings()
    except ValidationError as exc:
        problems = ", ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors(include_input=False)
        )
        raise SettingsError(f"invalid investigation-agent configuration: {problems}") from exc


__all__ = [
    "CONFIG_DIR_ENV",
    "ENVIRONMENT_ENV",
    "POLICY_FIELDS",
    "PolicyYamlSource",
    "Settings",
    "SettingsError",
    "config_file",
    "load_settings",
]
