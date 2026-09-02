"""OpenTelemetry lifecycle, span helpers, and structlog correlation shared by services.

Importing this package has no side effect. Services call `configure_observability`
and `configure_logging` from their composition root and `shutdown_observability` on
every exit path. The LangChain callback lives in `observability.langchain` behind the
`langchain` extra and is imported only by services that talk to a model.
"""

from observability.config import ExceptionDetail, LogDelivery, LoggingConfig, ObservabilityConfig
from observability.logging import configure_logging, get_logger, redact_credentials
from observability.providers import (
    ObservabilityConfigurationError,
    Providers,
    configure_observability,
    current_providers,
    shutdown_observability,
)
from observability.spans import (
    ERROR_TYPE,
    GENAI_CATEGORY,
    TELEMETRY_CATEGORY,
    WORKFLOW_RUN_ID,
    error_type_of,
    genai_span_attributes,
    mark_failed,
    start_genai_span,
    start_span,
    workflow_run,
)

__all__ = [
    "ERROR_TYPE",
    "ExceptionDetail",
    "GENAI_CATEGORY",
    "LogDelivery",
    "LoggingConfig",
    "ObservabilityConfig",
    "ObservabilityConfigurationError",
    "Providers",
    "TELEMETRY_CATEGORY",
    "WORKFLOW_RUN_ID",
    "configure_logging",
    "configure_observability",
    "current_providers",
    "error_type_of",
    "get_logger",
    "genai_span_attributes",
    "mark_failed",
    "redact_credentials",
    "shutdown_observability",
    "start_genai_span",
    "start_span",
    "workflow_run",
]
