## Purpose

Provide developers with a reproducible, local-only environment for collecting, retaining,
correlating, and exploring operational traces, metrics, and OTLP logs while keeping sensitive
GenAI content out of the operational trace store.

## ADDED Requirements

### Requirement: Complete local observability runtime
The local environment SHALL run a single pinned LGTM development service that bundles Grafana,
Loki, Tempo, Prometheus, and an OpenTelemetry Collector. Its telemetry stores SHALL use
single-container, local-filesystem configurations that are explicitly limited to development and
demonstration use.

#### Scenario: Start the observability runtime
- **WHEN** a developer starts the Compose project with valid local configuration
- **THEN** the LGTM service starts with the rest of the project
- **AND** Grafana, Loki, Tempo, Prometheus, and the Collector become available through that service

#### Scenario: Avoid production reuse
- **WHEN** a developer reviews the committed observability configuration
- **THEN** the documentation identifies the bundled, single-container runtime and filesystem storage as local-development-only

### Requirement: Vendor-neutral OTLP collection boundary
The local environment SHALL expose distinct operational and GenAI receiver boundaries over
OTLP/gRPC and OTLP/HTTP through the Collector bundled in the LGTM service. It SHALL retain all
development traces without sampling, route metrics to the bundled Prometheus and OTLP logs to the
bundled Loki, and attach a development environment resource attribute only when the producing
service did not supply one.

#### Scenario: Export from another Compose service
- **WHEN** a container sends supported telemetry to the applicable Collector endpoint
- **THEN** the Collector accepts the signal without requiring backend-specific configuration in the producing service
- **AND** routes it to the bundled backend responsible for that signal

#### Scenario: Export from the development host
- **WHEN** a host process sends OTLP traffic through a documented loopback endpoint
- **THEN** the Collector accepts the traffic without making the receiver reachable through a non-loopback host interface

#### Scenario: Preserve complete local traces
- **WHEN** the Collector accepts a complete operational or GenAI trace in the local environment
- **THEN** every destination receives the complete trace shape assigned to it without probabilistic, tail, or individual-span sampling

#### Scenario: Observe the Collector independently
- **WHEN** the Collector is running
- **THEN** its health endpoint reports process readiness separately from delivery success
- **AND** its internal metrics are queryable in Prometheus

### Requirement: Structured OTLP log collection
The local environment SHALL accept structured application logs over the operational OTLP
receiver and store them in Loki. Log records SHALL preserve the producing service identity and
trace and span identifiers when the producer supplies them. Automatic Docker stdout discovery
and Docker socket access SHALL NOT be required by the observability runtime.

#### Scenario: Collect an application log
- **WHEN** an application exports an OTLP log record through the operational receiver
- **THEN** the Collector forwards the record to Loki with its resource and log attributes

#### Scenario: Correlate a structured log
- **WHEN** an OTLP log record contains valid trace and span identifiers
- **THEN** Loki retains those identifiers so the record can be correlated with the corresponding Tempo trace

#### Scenario: Run without Docker socket access
- **WHEN** the LGTM service starts
- **THEN** it does not require the host Docker socket or visibility into unrelated containers

### Requirement: Provisioned cross-signal exploration
Grafana SHALL start with provisioned data sources for Loki, Tempo, and Prometheus. The
provisioning SHALL let a developer query all three signals without manual data-source setup and
SHALL support navigation between logs, traces, and metric exemplars when the relevant trace
identifiers or exemplar data are present.

#### Scenario: Use the stack without manual data-source setup
- **WHEN** Grafana becomes ready for the first time
- **THEN** Loki, Tempo, and Prometheus are already available as provisioned data sources

#### Scenario: Navigate from trace to logs
- **WHEN** a developer inspects a Tempo trace whose correlated logs are present in Loki
- **THEN** Grafana provides a query path to logs matching the trace identifier

#### Scenario: Navigate from log to trace
- **WHEN** a Loki log record contains a valid trace identifier
- **THEN** Grafana provides a link or query path to the corresponding trace in Tempo

#### Scenario: Navigate from metric exemplar to trace
- **WHEN** a Prometheus metric contains an exemplar with a valid trace identifier
- **THEN** Grafana provides a link to the corresponding Tempo trace

### Requirement: Local containment and least exposure
The observability runtime SHALL publish developer-facing endpoints only through host loopback.
Backend ports not required by host tools SHALL remain internal to the LGTM container or Compose
network, and the runtime SHALL operate without a Docker socket mount.

#### Scenario: Access local dashboards
- **WHEN** a developer opens the documented Grafana URL from the same host
- **THEN** the Grafana UI is reachable through `127.0.0.1`

#### Scenario: Prevent LAN telemetry access
- **WHEN** another machine attempts to connect through the development host's LAN address
- **THEN** the Compose port bindings do not accept Grafana or Collector traffic

#### Scenario: Avoid host-wide container visibility
- **WHEN** the observability runtime collects application telemetry
- **THEN** it does so through OTLP without access to host Docker metadata or logs

### Requirement: Persistent and reproducible local telemetry state
The local environment SHALL retain bundled Grafana, Loki, Tempo, and Prometheus state across
ordinary Compose shutdown and restart operations using one named volume. The LGTM image SHALL be
pinned by version and immutable digest in committed environment configuration, and the repository
SHALL document an explicit destructive reset for disposable local telemetry data.

#### Scenario: Restart without losing telemetry
- **WHEN** a developer stops the project without deleting volumes and starts it again
- **THEN** previously retained dashboards, logs, traces, and metrics remain available within each backend's configured retention behavior

#### Scenario: Reset local telemetry
- **WHEN** a developer invokes the documented destructive volume reset
- **THEN** local observability data is removed together with the other Compose-owned persistent state

#### Scenario: Resolve a reproducible image
- **WHEN** a developer prepares configuration from the committed environment example
- **THEN** Compose resolves an explicit LGTM version and immutable digest without using `latest`

### Requirement: Separate operational and content-bearing trace paths
The Collector SHALL provide a general operational trace receiver and a dedicated GenAI trace
receiver. General operational traces SHALL reach Tempo and SHALL NOT reach Langfuse. Complete
GenAI traces SHALL reach both a redacted Tempo pipeline and an authenticated Langfuse pipeline.
The Tempo pipeline MUST delete `gen_ai.system_instructions`, `gen_ai.input.messages`,
`gen_ai.output.messages`, `gen_ai.tool.definitions`, `gen_ai.tool.call.arguments`, and
`gen_ai.tool.call.result`; it SHALL retain non-content operational attributes including model
identity, latency, time to first chunk, token usage, finish reasons, and bounded error identity.
The Langfuse pipeline SHALL preserve the six content attributes when the producing application
captured them. Application content capture SHALL remain disabled by default and opt-in. The
existing direct authenticated Langfuse OTLP/HTTP boundary SHALL remain available independently of
the Collector.

#### Scenario: Send operational telemetry
- **WHEN** an application exports a general trace to the operational receiver
- **THEN** the complete trace reaches Tempo without being forwarded to Langfuse

#### Scenario: Inspect a redacted GenAI trace operationally
- **WHEN** an application exports a complete GenAI trace containing captured content to the dedicated GenAI receiver
- **THEN** Tempo receives the complete root, HTTP, retrieval, tool, and model span structure
- **AND** none of the six content-bearing GenAI attributes is present in Tempo
- **AND** operational model, timing, token, finish-reason, and error attributes remain available

#### Scenario: Preserve GenAI content in Langfuse
- **WHEN** an application exports a complete GenAI trace containing captured content to the dedicated GenAI receiver with valid Langfuse project credentials configured
- **THEN** Langfuse receives the complete trace with its captured content attributes

#### Scenario: Keep content capture disabled by default
- **WHEN** the producing application has not explicitly enabled AI content capture
- **THEN** neither the Collector nor any backend receives prompts, model outputs, tool arguments, tool results, system instructions, or tool definitions from that application

#### Scenario: Reject invalid Collector-mediated Langfuse ingestion
- **WHEN** the Collector attempts to export a GenAI trace without valid Langfuse project credentials
- **THEN** Langfuse rejects the export
- **AND** the Collector reports the exporter failure without preventing operational telemetry delivery

#### Scenario: Send content-bearing GenAI traces directly
- **WHEN** an application intentionally exports Langfuse-compatible traces with valid project credentials to Langfuse
- **THEN** the existing direct Langfuse ingestion path remains available independently of the Collector

### Requirement: Documented end-to-end verification
The repository SHALL document configuration validation, startup and readiness checks, service
URLs, a non-destructive shutdown, a destructive reset, and signal-specific end-to-end checks. The
verification SHALL distinguish process health from successful backend ingestion.

#### Scenario: Validate without starting containers
- **WHEN** a developer runs the documented Compose validation command
- **THEN** configuration interpolation and Compose structure are checked without creating services

#### Scenario: Verify each signal path
- **WHEN** a developer follows the documented telemetry verification procedure
- **THEN** the procedure checks Collector health and confirms an operational trace in Tempo, a redacted complete GenAI trace in Tempo, a content-preserving complete GenAI trace in Langfuse, a metric in Prometheus, and an OTLP log in Loki
- **AND** it verifies that an operational trace is absent from Langfuse and every prohibited GenAI payload attribute is absent from Tempo
