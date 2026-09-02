## 1. Configuration Contract

- [x] 1.1 Reconfirm the selected Langfuse v4 and backing-service image versions against the current upstream Compose, migration requirements, and host architecture; record explicit non-`latest` tags or digests and verify each image exposes a compatible manifest.
- [x] 1.2 Add `.env.example` with version variables, required secret names and generation guidance, plus a clearly grouped optional headless-initialization set; verify the example contains no usable deployment credentials and documents every variable referenced by Compose.
- [x] 1.3 Add `.env` to `.gitignore` without disturbing existing rules and verify `git check-ignore .env` succeeds while `.env.example` remains trackable.

## 2. Local Langfuse Compose Stack

- [x] 2.1 Add root `compose.yaml` definitions for PostgreSQL, ClickHouse, Redis, and MinIO with named volumes, internal networking, compatible pinned images, and bounded health checks; verify the resolved Compose model contains all four healthy dependencies and no unintended host port publishing.
- [x] 2.2 Add Langfuse web and worker services using the same pinned v4 release, a shared environment contract, healthy dependency conditions, documented health endpoints, and optional initialization pass-through; verify both services resolve identical database, queue, object-store, encryption, and `TELEMETRY_ENABLED=false` values where required.
- [x] 2.3 Publish only Langfuse web and the browser-required MinIO API on explicit `127.0.0.1` bindings, and verify the resolved Compose port mappings expose neither databases, Redis, the worker, nor MinIO administration.
- [x] 2.4 Validate fail-fast configuration behavior by confirming `docker compose config --quiet` rejects each missing required secret and succeeds with a complete generated local `.env`.

## 3. Developer Documentation

- [x] 3.1 Extend `README.md` with Docker prerequisites and resource guidance, environment preparation, first startup, expected delay, local URLs, public health/readiness checks, and non-destructive shutdown; verify every documented command matches the final Compose service names and ports.
- [x] 3.2 Document optional UI-first and headless initialization, including create-once semantics, and verify the instructions provide all-or-nothing initialization values without presenting them as credential rotation.
- [x] 3.3 Document the future direct Langfuse SDK/OTLP-over-HTTP trace boundary, Basic authentication, v4 ingestion header, host and Compose-network endpoints, and the deliberate absence of Collector, metrics, and log routing; verify the endpoint paths match the running v4 service.
- [x] 3.4 Document destructive reset separately from ordinary shutdown with an explicit data-loss warning, and verify the reset command targets only this Compose project's named volumes.

## 4. Runtime and Contract Verification

- [x] 4.1 With Docker Desktop allocated sufficient resources, start the project using `docker compose up -d --wait` and verify all six containers are running or healthy and `GET /api/public/ready` returns success.
- [x] 4.2 Verify local containment by reaching the UI and MinIO API through loopback and confirming no published stack port accepts a connection through a non-loopback host address.
- [x] 4.3 Exercise either the configured headless bootstrap or UI-first flow, send one authenticated v4 OTLP/HTTP trace, confirm it appears in the intended Langfuse project, and confirm an unauthenticated ingestion request is rejected.
- [x] 4.4 Stop and recreate containers without deleting volumes, then verify the initialized project and test trace persist; perform any destructive reset check only against explicitly disposable test volumes.
- [x] 4.5 Run strict OpenSpec validation and a final repository diff review, verifying the implementation satisfies every `local-langfuse` scenario without adding agent code, Python dependencies, custom service configuration, or an OpenTelemetry Collector.
