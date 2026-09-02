## Context

See `proposal.md` for motivation. The repository is a Python `uv` workspace with only the
synthetic dataset package implemented; it has no runtime service, container definition, or
telemetry configuration. The future investigation agent is architecturally described, but its
framework, provider, and process boundary remain intentionally undecided.

Langfuse v4 self-hosting is not a two-container application. Its supported local topology uses
separate web and worker application containers plus PostgreSQL, ClickHouse, Redis, and S3-compatible
object storage. The Docker and Compose CLIs are installed on the development host, although the
Docker engine was not running during design discovery, so available Docker Desktop resources could
not yet be measured.

## Goals / Non-Goals

**Goals:**

- Provide one repository-owned Compose project that can be started and inspected locally.
- Preserve local traces and Langfuse configuration across normal restarts.
- Keep credentials outside committed configuration and bind host access to loopback.
- Establish a direct, configurable trace destination that does not constrain the future agent
  framework.
- Stay close enough to the upstream Langfuse v4 Compose topology that upgrades remain legible.

**Non-Goals:**

- Instrumenting or selecting a framework for the future agent.
- Collecting operational metrics or logs, adding an OpenTelemetry Collector, or routing to a
  second observability backend.
- Production high availability, TLS termination, backups, retention policy, autoscaling, or
  disaster recovery.
- Providing a shared database for application data; all backing services in this stack belong to
  Langfuse.
- Adding custom images or custom PostgreSQL, ClickHouse, Redis, or MinIO configuration files.

## Decisions

### Keep the stack in the repository root

Create `compose.yaml` at the repository root and keep all service configuration in that file. Add
only `.env.example`, an ignored `.env`, and concise README instructions. Do not create `services/`
until an actual application service exists or a component requires owned configuration or a
custom image.

This keeps `docker compose` commands conventional and avoids giving third-party infrastructure the
same source layout as future first-party services. A dedicated `services/langfuse/` directory and
cloning the upstream Langfuse repository were rejected because neither adds a boundary the project
currently needs.

### Use the complete upstream-compatible v4 topology

The Compose project contains these responsibilities:

```text
future agent --SDK/OTLP-HTTP--> langfuse-web ----> PostgreSQL
                                      |
                                      +----------> MinIO
                                      |
                                      +----------> Redis ----> langfuse-worker
                                                                  |
                                                                  v
                                                             ClickHouse
```

- Langfuse web owns the UI and public APIs.
- Langfuse worker owns asynchronous event processing.
- PostgreSQL stores transactional metadata such as users, organizations, projects, and settings.
- ClickHouse stores trace observations and analytical event data.
- Redis supplies queues and cache state.
- MinIO supplies S3-compatible raw-event and media storage.

Removing a backing component or substituting the older Langfuse v2 shape was rejected because it
would not represent the current self-hosted product.

### Pin compatible container versions explicitly

Use an exact Langfuse v4 release variable for both web and worker; the initial implementation
candidate is `4.22.0`, the current release during discovery. Use explicit v4-compatible image
versions for PostgreSQL, ClickHouse, Redis, and MinIO, avoiding `latest` and mutable major-only tags
where an immutable version or digest is available. Web and worker MUST use the same Langfuse
version.

Exact application pinning makes local failures reproducible and turns upgrades into reviewed
changes. Tracking the mutable upstream `:4` tag was rejected because it can change the data schema
or behavior between two developer starts.

### Externalize secrets in one ignored environment file

`compose.yaml` references required secrets with Compose's required-value interpolation so missing
values fail during configuration resolution. `.env.example` documents variable names and generation
commands but contains no usable deployment credentials. `.env` is the conventional active local
file and is added to `.gitignore`.

The required secret set includes the Langfuse authentication secret, salt, 32-byte encryption key,
PostgreSQL password, ClickHouse password, Redis password, and MinIO root password. A shared YAML
environment anchor keeps the Langfuse web and worker configuration identical where required.

Committing convenient fixed passwords was rejected even for loopback-only use because the Compose
file may later be reused or exposed in a less constrained environment.

### Make headless initialization optional and all-or-nothing

Pass through Langfuse's organization, project, owner, and project-key initialization variables only
when developers set them. The example groups the full set and explains that partial configuration is
invalid. Omitting the group leaves the normal UI-first signup flow available.

Headless values create resources only when they do not exist; changing them is not a credential
rotation mechanism. This avoids surprising destructive behavior while enabling repeatable local
smoke tests later.

### Publish the smallest loopback-only surface

Publish Langfuse web on `127.0.0.1:3000` and the MinIO S3 API needed by browser media operations on
`127.0.0.1:9090`. Keep the MinIO administration console, worker health port, PostgreSQL, ClickHouse,
and Redis available only on the Compose network unless a future diagnostic requirement justifies a
temporary loopback mapping.

Binding every upstream diagnostic port to the host was rejected because container-to-container
traffic needs no host publishing and the extra surface is easy to misuse.

### Combine dependency health with public readiness

Give every backing service a bounded health check and make both Langfuse application containers
depend on healthy backing services. Add health checks for the worker and the web service using their
documented endpoints. Runtime acceptance uses the web readiness endpoint, not merely container
process state. Use named volumes for all backing stores and a local-friendly restart policy.

The startup may take several minutes on the first image pull and migration. Generous start periods
and retries prevent expected migrations from looking like terminal failures.

### Export agent traces directly for the first milestone

The future agent will use either the OpenTelemetry-native Langfuse Python SDK or an OTLP/HTTP
exporter pointed at `http://localhost:3000/api/public/otel/v1/traces` when it runs on the host. A
containerized agent will use the Compose service hostname instead. Generic OTLP exporters must use
project-key Basic authentication and the v4 ingestion header.

Do not add a Collector until there is a concrete need for fan-out, central credential ownership,
redaction, transformation, protocol conversion, tail sampling, or stronger buffering. Keeping the
endpoint configurable preserves that future migration path.

### Default optional Langfuse deployment telemetry to off

Set `TELEMETRY_ENABLED=false` in the shared web/worker environment. This affects Langfuse's own
aggregated deployment telemetry; it does not disable ingestion of the agent traces the developer
explicitly sends to the local instance.

## Risks / Trade-offs

- **The six-service stack is resource-heavy** -> Document the upstream development sizing guidance,
  verify Docker Desktop CPU/memory allocation before runtime testing, and report a clear readiness
  failure rather than weakening the topology.
- **Pinned images become stale** -> Keep versions centralized, update them explicitly against the
  current upstream Compose and migration notes, and validate each upgrade with a fresh and a
  persisted-volume start.
- **First startup is slow** -> Use dependency health checks with migration-aware start periods and
  document that the first pull/start can take several minutes.
- **Local traces can contain sensitive evidence** -> Bind services to loopback, keep secrets ignored,
  disable deployment telemetry, and leave prompt/tool content capture off when agent instrumentation
  is designed unless it is explicitly approved.
- **`docker compose down -v` permanently deletes local traces** -> Separate ordinary shutdown from
  destructive reset in the documentation and label the latter clearly.
- **Headless initialization values are not rotation controls** -> Document their create-once behavior;
  use Langfuse administration flows or an intentional local reset for later changes.
- **Direct export does not provide centralized fan-out or processing** -> Accept this for the single
  local trace backend and introduce a Collector only when a named requirement appears.

## Migration Plan

1. Add the ignored environment contract and root Compose project without changing Python packages.
2. Validate the resolved Compose configuration before starting containers.
3. Start the stack, wait for dependency health and Langfuse readiness, then exercise the public
   health endpoint and login/initialization path.
4. Restart without deleting volumes and verify that the initialized project remains present.
5. Document direct OTLP/HTTP settings for the future agent without adding agent dependencies.

Rollback is non-destructive by default: stop and remove the Compose containers while retaining named
volumes. Removing the new repository files returns the repository workflow to its previous state.
Volume deletion is a separate, explicitly destructive local reset and is not part of rollback.
