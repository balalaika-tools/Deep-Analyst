## ADDED Requirements

### Requirement: Agent persistence shares the application PostgreSQL deployment
The investigation service SHALL use the existing application PostgreSQL service rather than a
second database container. Within that physical service, canonical evidence stays in its existing
schema, model-visible read views live under `agent_read`, and LangGraph checkpoint objects live
under `agent_runtime`. An ordinary Compose stop and restart SHALL preserve all three areas in the
existing application database volume.

#### Scenario: Agent startup adds no database container
- **WHEN** the local investigation profile is rendered with Compose
- **THEN** evidence, views, and checkpoints use `postgres-app` and no second PostgreSQL service or
  volume is created

#### Scenario: Restart preserves checkpoints
- **WHEN** a thread has checkpoints and Compose is restarted without deleting volumes
- **THEN** the thread's history and recoverable graph state remain available

### Requirement: Two purpose-specific runtime roles and pools
Database DDL SHALL be performed only by the initialization step using the existing database owner
credential, which the running investigation service SHALL NOT receive. The service SHALL use an
`agent_reader` credential for evidence access and an `agent_writer` credential for checkpoints.
`agent_reader` SHALL have `SELECT` only on the evidence tables and projections the trusted
retrieval and graph code needs and on the `agent_read` views, SHALL default to read-only
transactions, and SHALL have no `TEMP`, `CREATE`, or `agent_runtime` privilege. `agent_writer`
SHALL have privileges only on `agent_runtime` and none on evidence. Ingestion credentials SHALL
receive no agent-schema grant. The service SHALL construct one async connection pool per role and
SHALL NOT execute model-authored SQL through the writer pool.

#### Scenario: Reader cannot write application state
- **WHEN** the `agent_reader` credential attempts to read or mutate an `agent_runtime` object or
  to write evidence
- **THEN** PostgreSQL rejects the operation for insufficient privilege

#### Scenario: Writer cannot read evidence
- **WHEN** the `agent_writer` credential attempts to select from evidence tables or `agent_read`
- **THEN** PostgreSQL rejects the operation for insufficient privilege

#### Scenario: Model SQL uses only the reader pool
- **WHEN** `query_records` executes an approved model-authored statement
- **THEN** it uses the `agent_reader` pool inside a read-only transaction

### Requirement: Controlled schema and checkpointer initialization
A one-shot idempotent `agent-db-init` step SHALL, using the owner credential and after ingestion
has completed or skipped: verify the expected evidence objects exist; create the two runtime login
roles with passwords from its environment; create the `agent_read` schema and views; create the
`agent_runtime` schema; run `AsyncPostgresSaver.setup()` with `search_path` set to
`agent_runtime`; apply grants; and record the applied initializer version in
`agent_runtime.schema_version`. It SHALL NOT run in an HTTP request or in the service's ordinary
startup. Re-running at the same version SHALL be a no-op that drops nothing.

#### Scenario: Agent schemas are initialized after evidence
- **WHEN** the initializer runs after compatible evidence ingestion against a database with no
  agent-owned schemas
- **THEN** it creates the roles, views, checkpoint objects, grants, and version record and exits
  successfully

#### Scenario: Evidence schema is not ready
- **WHEN** the initializer cannot find the required evidence tables or indexes
- **THEN** it fails closed before creating the read views or starting the API

#### Scenario: Initialization is idempotent
- **WHEN** the initializer runs again at the expected version
- **THEN** it exits successfully without dropping data, duplicating objects, or weakening grants

#### Scenario: Runtime credential cannot perform DDL
- **WHEN** the running investigation service attempts to create or alter a database object
- **THEN** PostgreSQL rejects the operation for insufficient privilege

### Requirement: Agent service dependency readiness
Compose SHALL start the investigation service only after `postgres-app` is healthy, ingestion has
completed or skipped successfully, and `agent-db-init` has completed successfully. The service
readiness check SHALL verify both pools, the recorded initializer version, the `agent_read` views,
and availability of the configured lexical and vector search dependencies. A failed check SHALL
keep readiness false without changing schema or application data.

#### Scenario: Uninitialized checkpoint storage blocks readiness
- **WHEN** the initializer version record is missing or does not match the expected version
- **THEN** the investigation service remains not ready and performs no request-time setup

#### Scenario: Healthy dependencies make the service ready
- **WHEN** both pools connect with their intended privileges, the version matches, and search
  dependencies are available
- **THEN** the investigation service reports ready without writing a probe row

### Requirement: Agent service configuration is typed, layered, and secret-safe
Configuration SHALL resolve in this precedence order: explicit constructor input, environment,
`.env`, the selected `config/investigation-agent/<environment>.yaml`, then typed code defaults.
Committed YAML SHALL accept only allowlisted non-secret policy. Deployment endpoints, model IDs,
the expected initializer version, and credential locations SHALL require environment or
secret-source input when no explicitly safe default exists. The serving
process SHALL receive only the `agent_reader` and `agent_writer` DSNs as `SecretStr`; the
initializer SHALL receive only the owner DSN and the two role passwords. AWS credentials SHALL
remain in the provider SDK chain. Each entry point SHALL validate its configuration before
initializing telemetry, pools, saver, or model clients and SHALL fail closed on missing, invalid,
unknown, or misplaced secret fields.

#### Scenario: Configuration sources disagree
- **WHEN** one non-secret setting is supplied by constructor, environment, `.env`, YAML, and default
- **THEN** the constructor value wins, followed in order by environment, `.env`, YAML, and default

#### Scenario: Secret is placed in YAML
- **WHEN** a DSN password appears in committed YAML
- **THEN** configuration rejects the key as outside the YAML allowlist
- **AND** the validation error does not render its value

#### Scenario: Serving container lacks owner authority
- **WHEN** the investigation-service container environment is inspected
- **THEN** it contains no owner credential and exposes only the reader and writer connection
  settings

#### Scenario: Invalid configuration has no external side effect
- **WHEN** a required endpoint, model identifier, credential, or bound is invalid
- **THEN** the entry point fails before telemetry, database pools, saver, or provider clients are
  constructed
