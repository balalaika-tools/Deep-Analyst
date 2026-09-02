## Purpose

Provide developers with a reproducible, persistent, and locally contained Langfuse environment
for inspecting future agent traces before production observability architecture is introduced.

## ADDED Requirements

### Requirement: Complete local Langfuse runtime
The local environment SHALL run a compatible Langfuse v4 web service, Langfuse worker,
PostgreSQL database, ClickHouse database, Redis queue and cache, and S3-compatible object store
as one Docker Compose project.

#### Scenario: Start the complete stack
- **WHEN** a developer starts the local Compose project with valid configuration
- **THEN** all six services start and the Langfuse web service becomes ready after its backing services are healthy

#### Scenario: Reject an incomplete dependency startup
- **WHEN** a required backing service cannot become healthy
- **THEN** the Langfuse services do not report the deployment as ready

### Requirement: Local-only network exposure
The local environment SHALL make the Langfuse browser and ingestion API, and any browser-required
object-storage endpoint, available only through the host loopback interface. PostgreSQL,
ClickHouse, Redis, worker internals, and object-storage administration SHALL remain inaccessible
through non-loopback host interfaces.

#### Scenario: Access Langfuse from the development host
- **WHEN** the stack is ready
- **THEN** a developer can open the Langfuse UI and call its public health and ingestion APIs from the same host

#### Scenario: Prevent LAN exposure
- **WHEN** another machine attempts to connect to a published stack port through the development host's LAN address
- **THEN** the Compose port bindings do not accept the connection

### Requirement: Persistent local state
The local environment SHALL retain Langfuse metadata, trace data, queued data, and object data
across ordinary Compose stop, removal, and restart operations unless the developer explicitly
requests a destructive volume reset.

#### Scenario: Restart without losing data
- **WHEN** a developer stops the Compose project without deleting volumes and starts it again
- **THEN** previously created users, projects, API keys, and ingested traces remain available

#### Scenario: Explicitly reset local data
- **WHEN** a developer invokes the documented destructive reset operation
- **THEN** the stack removes its named volumes and the next startup creates a fresh Langfuse instance

### Requirement: Externalized secrets and versions
The local environment SHALL obtain credentials, encryption material, initialization values, and
container versions from developer-controlled environment configuration. The repository MUST NOT
commit an active local environment file or usable deployment secrets, and startup SHALL fail
clearly when required secret values are absent or still invalid placeholders.

#### Scenario: Prepare local configuration
- **WHEN** a developer copies the committed environment example and supplies valid local secrets
- **THEN** Compose resolves a complete configuration without requiring edits to the Compose definition

#### Scenario: Protect active local secrets
- **WHEN** a developer creates the active local environment file at the documented path
- **THEN** repository ignore rules exclude that file from ordinary version-control discovery

#### Scenario: Detect missing required secrets
- **WHEN** a developer starts or validates the stack without a required secret
- **THEN** configuration resolution fails with a message identifying the missing value

### Requirement: Reproducible optional initialization
The local environment SHALL allow a developer to initialize one organization, project, owner,
and project API-key pair from a complete environment configuration. Initialization SHALL be
optional and SHALL NOT embed working credentials in committed files.

#### Scenario: Initialize a configured development project
- **WHEN** all required initialization values are supplied on the first startup
- **THEN** Langfuse creates the configured organization, project, owner, and API keys for local use

#### Scenario: Use interactive initialization
- **WHEN** initialization values are omitted
- **THEN** the stack becomes ready and permits the developer to create the first account and project through the Langfuse UI

### Requirement: Direct trace-ingestion boundary
The ready local environment SHALL expose Langfuse's authenticated OTLP-over-HTTP trace-ingestion
boundary so a future agent can export Langfuse-compatible OpenTelemetry traces directly, without
requiring an OpenTelemetry Collector in this local stack.

#### Scenario: Ingest an authenticated trace
- **WHEN** a client sends a complete OTLP trace to the documented local endpoint using valid project credentials and the supported ingestion version
- **THEN** Langfuse accepts the trace and makes it available in the configured project

#### Scenario: Reject unauthenticated ingestion
- **WHEN** a client sends a trace without valid project authentication
- **THEN** Langfuse rejects the ingestion request without storing it in a project

### Requirement: Private-by-default local operation
The local Langfuse application containers SHALL disable optional outbound Langfuse deployment
telemetry by default. The stack SHALL NOT configure external LLM providers, prompt execution, or
operational metrics and log backends as part of this capability.

#### Scenario: Start with deployment telemetry disabled
- **WHEN** the local stack starts with the committed default configuration
- **THEN** both Langfuse application containers have optional deployment telemetry disabled

### Requirement: Documented lifecycle and verification
The repository SHALL document prerequisites, configuration, startup, readiness verification,
service URLs, non-destructive shutdown, destructive reset, and the future local trace endpoint.
The documented verification SHALL distinguish configuration validity from runtime readiness.

#### Scenario: Validate configuration without starting services
- **WHEN** a developer follows the documented configuration-validation command
- **THEN** Docker Compose validates the resolved project without creating containers

#### Scenario: Verify runtime readiness
- **WHEN** a developer follows the documented readiness check after startup
- **THEN** the check succeeds only when the Langfuse web service reports that it is ready to receive traffic
