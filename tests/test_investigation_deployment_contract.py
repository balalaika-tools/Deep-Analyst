from pathlib import Path
from typing import Any, cast

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = REPO_ROOT / "compose.yaml"
DOCKERFILE_PATH = REPO_ROOT / "services" / "investigation_agent" / "Dockerfile"
WEB_DOCKERFILE_PATH = REPO_ROOT / "services" / "investigation_web" / "Dockerfile"


def _compose() -> dict[str, Any]:
    loaded = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise TypeError("compose.yaml must contain a mapping")
    return cast(dict[str, Any], loaded)


def test_investigation_image_is_scoped_pinned_and_non_root() -> None:
    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
    runtime = dockerfile.split("FROM python-base AS runtime", maxsplit=1)[1]

    assert "ARG PYTHON_VERSION=3.13.14" in dockerfile
    assert "ARG UV_VERSION=0.12.4" in dockerfile
    assert "FROM python:${PYTHON_VERSION}-slim-trixie AS python-base" in dockerfile
    assert "COPY .python-version pyproject.toml uv.lock ./" in dockerfile
    for metadata in (
        "services/ingestion/pyproject.toml",
        "services/investigation_agent/pyproject.toml",
        "libs/evidence_model/pyproject.toml",
        "libs/observability/pyproject.toml",
        "data/dataset/pyproject.toml",
    ):
        assert f"COPY {metadata} {metadata}" in dockerfile
    assert dockerfile.count("--package investigation-agent") == 2
    assert dockerfile.count("--no-dev") == 2
    assert "--no-install-workspace" in dockerfile
    assert "--no-editable" in dockerfile
    assert "COPY libs/evidence_model libs/evidence_model" in dockerfile
    assert "COPY libs/observability libs/observability" in dockerfile

    assert "ARG APP_UID=10001" in runtime
    assert "ARG APP_GID=10001" in runtime
    assert "USER ${APP_UID}:${APP_GID}" in runtime
    assert 'ENTRYPOINT ["/usr/bin/tini", "--"]' in runtime
    assert 'CMD ["/app/.venv/bin/investigation-agent"]' in runtime
    assert "http://127.0.0.1:8080/health" in runtime
    assert "COPY --from=uv" not in runtime
    assert "AGENT_OWNER_DATABASE_URL" not in dockerfile
    assert "POSTGRES_APP_PASSWORD" not in dockerfile


def test_dockerignore_keeps_version_pin_and_excludes_local_environment() -> None:
    rules = {
        line.strip()
        for line in (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert ".python-version" not in rules
    assert ".venv" in rules
    assert ".env" in rules
    assert "**/tests" in rules


def test_compose_orders_one_initializer_and_one_agent_on_existing_database() -> None:
    compose = _compose()
    services = compose["services"]
    volumes = compose["volumes"]
    assert isinstance(services, dict)
    assert isinstance(volumes, dict)

    agent_services = {name for name in services if "agent" in name}
    assert agent_services == {"agent-db-init", "investigation-agent"}
    assert "postgres-app" in services
    assert "app_postgres_data" in volumes
    assert not any("agent" in name for name in volumes)

    initializer = services["agent-db-init"]
    serving = services["investigation-agent"]
    assert initializer["restart"] == "no"
    assert initializer["depends_on"] == {
        "postgres-app": {"condition": "service_healthy"},
        "ingestion": {"condition": "service_completed_successfully"},
    }
    assert serving["depends_on"] == {
        "postgres-app": {"condition": "service_healthy"},
        "ingestion": {"condition": "service_completed_successfully"},
        "agent-db-init": {"condition": "service_completed_successfully"},
    }


def test_frontend_starts_independently_and_proxies_only_to_serving_agent() -> None:
    web = _compose()["services"]["investigation-web"]
    dockerfile = WEB_DOCKERFILE_PATH.read_text(encoding="utf-8")

    assert "depends_on" not in web
    assert web["environment"] == {
        "INVESTIGATION_AGENT_URL": (
            "http://investigation-agent:"
            "${INVESTIGATION_AGENT_PORT:?Set INVESTIGATION_AGENT_PORT in .env}"
        )
    }
    assert web["ports"] == ["127.0.0.1:${INVESTIGATION_WEB_PORT:-3002}:3000"]
    assert "COPY --from=builder --chown=node:node /app/scripts ./scripts" in dockerfile


def test_compose_separates_initializer_and_serving_credentials() -> None:
    services = _compose()["services"]
    initializer_environment = services["agent-db-init"]["environment"]
    serving_environment = services["investigation-agent"]["environment"]

    assert {
        "AGENT_OWNER_DATABASE_URL",
        "AGENT_READER_PASSWORD",
        "AGENT_WRITER_PASSWORD",
    } <= initializer_environment.keys()
    assert "AGENT_READER_DATABASE_URL" not in initializer_environment
    assert "AGENT_WRITER_DATABASE_URL" not in initializer_environment
    assert {"AGENT_READER_DATABASE_URL", "AGENT_WRITER_DATABASE_URL"} <= (
        serving_environment.keys()
    )
    assert "AGENT_OWNER_DATABASE_URL" not in serving_environment
    assert "AGENT_READER_PASSWORD" not in serving_environment
    assert "AGENT_WRITER_PASSWORD" not in serving_environment
    assert "POSTGRES_APP_PASSWORD" not in serving_environment


def test_compose_wires_runtime_bounds_readiness_models_and_otlp() -> None:
    serving = _compose()["services"]["investigation-agent"]
    environment = serving["environment"]
    healthcheck = " ".join(serving["healthcheck"]["test"])

    assert serving["ports"] == [
        "127.0.0.1:${INVESTIGATION_AGENT_PORT:?Set INVESTIGATION_AGENT_PORT in .env}:"
        "${INVESTIGATION_AGENT_PORT:?Set INVESTIGATION_AGENT_PORT in .env}"
    ]
    assert serving["stop_grace_period"] == "35s"
    assert "/ready" in healthcheck
    assert "./config/investigation-agent:/app/config/investigation-agent:ro" in serving["volumes"]
    for name in (
        "READER_POOL_MIN_SIZE",
        "READER_POOL_MAX_SIZE",
        "WRITER_POOL_MIN_SIZE",
        "WRITER_POOL_MAX_SIZE",
        "POOL_ACQUIRE_TIMEOUT_S",
        "READINESS_TIMEOUT_S",
        "SHUTDOWN_TIMEOUT_S",
        "AWS_REGION",
        "BEDROCK_CHAT_MODEL_ID",
        "BEDROCK_EMBEDDING_MODEL_ID",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "CAPTURE_AI_CONTENT",
    ):
        assert name in environment


def test_investigation_documentation_covers_runtime_contracts() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    design = (REPO_ROOT / "docs" / "DESIGN.md").read_text(encoding="utf-8")
    data_model = (REPO_ROOT / "docs" / "DATA_MODEL.md").read_text(encoding="utf-8")
    combined = "\n".join((readme, design, data_model)).lower()

    for required in (
        "one langchain `create_agent`",
        "nested `create_agent`",
        "hybrid",
        "agent_read",
        "app.case_id",
        "checkpoint",
        "answer.delta",
        "source reference",
        "single-replica",
        "forced row-level security",
        "database leases",
        "no authentication or authorization layer",
        "delete /v1/threads/{thread_id}",
    ):
        assert required in combined
    for command in (
        "docker compose up --build --wait agent-db-init",
        "docker compose up -d --wait investigation-agent",
        "http://localhost:8080/v1/agent/invoke",
        "http://localhost:8080/v1/threads",
    ):
        assert command in readme
