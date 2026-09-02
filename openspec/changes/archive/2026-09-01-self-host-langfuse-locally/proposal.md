## Why

The future investigation chatbot needs a local, inspectable destination for agent traces,
but the repository has no observability runtime today. Establishing Langfuse now provides a
stable development boundary before the agent framework and instrumentation are selected.

## What Changes

- Add a local-only Docker Compose stack for Langfuse v4 and its required PostgreSQL,
  ClickHouse, Redis, and MinIO dependencies.
- Persist service data across ordinary restarts, gate startup on dependency health, and expose
  only the browser/API endpoints needed from the host loopback interface.
- Add environment-variable based secret and version configuration, with an ignored local
  environment file and a committed example.
- Support reproducible optional initialization of a local organization, project, user, and
  project API keys.
- Document startup, readiness verification, shutdown, destructive volume reset, and the future
  direct Langfuse SDK/OTLP trace endpoint.
- Keep operational metrics, logs, production availability, backups, and an OpenTelemetry
  Collector outside this change.

## Capabilities

### New Capabilities

- `local-langfuse`: Run, configure, verify, persist, and safely access a self-hosted Langfuse
  development stack.

### Modified Capabilities

None.

## Impact

- Adds root development-infrastructure files: `compose.yaml`, `.env.example`, and related
  `.gitignore` and `README.md` updates.
- Introduces local container dependencies for Langfuse web and worker, PostgreSQL, ClickHouse,
  Redis, and MinIO; it does not add Python runtime dependencies or agent code.
- Requires Docker Compose and sufficient local CPU, memory, and disk for the six-service stack.
- Reserves the Langfuse PostgreSQL instance for Langfuse metadata; future application storage
  remains a separate concern.
