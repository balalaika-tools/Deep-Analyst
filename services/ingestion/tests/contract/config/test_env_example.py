"""The service's .env.example must document exactly the settings contract."""

import re
from pathlib import Path

from ingestion.config.settings import POLICY_FIELDS, Settings
from pydantic_core import PydanticUndefined

ENV_EXAMPLE = Path(__file__).resolve().parents[3] / ".env.example"
CREDENTIAL_PASSTHROUGH = {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"}
FORBIDDEN_LEGACY_NAMES = {"EMBEDDING_BATCH_SIZE", "GENAI_OTLP_TRACES_ENDPOINT"}


def _sections() -> dict[str, set[str]]:
    sections: dict[str, set[str]] = {}
    current = "preamble"
    for line in ENV_EXAMPLE.read_text().splitlines():
        header = re.match(r"^# (REQUIRED|OPTIONAL|OVERRIDABLE)\b", line)
        if header:
            current = header.group(1)
            sections.setdefault(current, set())
            continue
        match = re.match(r"^#?\s*([A-Z][A-Z0-9_]+)=", line)
        if match:
            sections.setdefault(current, set()).add(match.group(1))
    return sections


def _aliases(names: frozenset[str] | set[str]) -> set[str]:
    return {str(Settings.model_fields[name].alias) for name in names}


def test_required_optional_and_overridable_sections_match_the_settings_class() -> None:
    sections = _sections()
    required = {
        name for name, field in Settings.model_fields.items() if field.default is PydanticUndefined
    }
    optional = set(Settings.model_fields) - required - POLICY_FIELDS

    assert sections["REQUIRED"] == _aliases(required) | CREDENTIAL_PASSTHROUGH
    assert sections["OVERRIDABLE"] == _aliases(POLICY_FIELDS)
    # Optional is the small set of runtime-only knobs, never "values with a default".
    assert sections["OPTIONAL"] == _aliases(optional) | {"INGESTION_CONFIG_DIR"}
    assert optional == {"service_instance_id"}
    assert "ENVIRONMENT_NAME" in sections["REQUIRED"]


def test_environment_contract_documents_only_the_single_ingestion_trace_endpoint() -> None:
    documented = set().union(*_sections().values())
    text = ENV_EXAMPLE.read_text()

    assert documented.isdisjoint(FORBIDDEN_LEGACY_NAMES)
    assert text.count("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=") == 1
