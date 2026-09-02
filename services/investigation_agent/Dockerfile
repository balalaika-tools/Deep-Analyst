# syntax=docker/dockerfile:1
# Build from the workspace root: docker build -f services/investigation_agent/Dockerfile .

ARG PYTHON_VERSION=3.13.14
ARG UV_VERSION=0.12.4

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv
FROM python:${PYTHON_VERSION}-slim-trixie AS python-base


FROM python-base AS builder

ARG PYTHON_VERSION

COPY --from=uv /uv /uvx /usr/local/bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_PROGRESS=1 \
    UV_PYTHON_DOWNLOADS=0

# Copy every member's metadata so --locked validates the complete workspace while
# dependency installation remains scoped to the investigation service.
COPY .python-version pyproject.toml uv.lock ./
COPY services/ingestion/pyproject.toml services/ingestion/pyproject.toml
COPY services/investigation_agent/pyproject.toml services/investigation_agent/pyproject.toml
COPY libs/evidence_model/pyproject.toml libs/evidence_model/pyproject.toml
COPY libs/observability/pyproject.toml libs/observability/pyproject.toml
COPY data/dataset/pyproject.toml data/dataset/pyproject.toml

# Docker cannot populate a pre-FROM ARG from .python-version. Keep the literal
# pins aligned and make drift fail immediately.
RUN test "$(tr -d '\r\n' < .python-version)" = "${PYTHON_VERSION}"

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync \
        --locked \
        --no-dev \
        --no-install-workspace \
        --package investigation-agent

# Copy only this service and the internal libraries in its dependency closure.
COPY services/investigation_agent services/investigation_agent
COPY libs/evidence_model libs/evidence_model
COPY libs/observability libs/observability

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync \
        --locked \
        --no-dev \
        --no-editable \
        --package investigation-agent


FROM python-base AS runtime

ARG APP_UID=10001
ARG APP_GID=10001

WORKDIR /app

ENV HOME=/home/appuser \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${APP_GID}" appuser \
    && useradd \
        --uid "${APP_UID}" \
        --gid "${APP_GID}" \
        --create-home \
        --home-dir /home/appuser \
        --shell /usr/sbin/nologin \
        --no-log-init \
        appuser

COPY --from=builder /app/.venv /app/.venv

USER ${APP_UID}:${APP_GID}

EXPOSE 8080
STOPSIGNAL SIGTERM

HEALTHCHECK \
    --interval=30s \
    --timeout=5s \
    --start-period=20s \
    --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3).close()"]

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/app/.venv/bin/investigation-agent"]
