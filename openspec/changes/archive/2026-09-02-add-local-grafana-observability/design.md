## Context

The repository's current unarchived implementation adds Grafana, Loki, Tempo, Mimir, a locally
built OpenTelemetry Collector, and Alloy as six standalone Compose services beside the existing
Langfuse v4 environment. That topology works, but it carries backend-specific configuration,
images, health checks, volumes, and Docker socket access that are unnecessary for the intended
local-development workflow. See `proposal.md` for the revised motivation and
`specs/local-observability/spec.md` for the behavioral contract.

The replacement must preserve the two OTLP boundaries and destination-specific GenAI content
policy, the existing direct Langfuse ingestion path, loopback-only publication, deterministic
image resolution, persistence, and end-to-end verification.

## Goals / Non-Goals

**Goals:**

- Collapse operational observability into one reproducible local service.
- Keep the Collector configuration repository-owned and independently testable.
- Preserve complete trace fan-out while redacting content only from Tempo-bound GenAI traces.
- Query traces, metrics, and OTLP logs through pre-provisioned Grafana data sources.
- Remove Docker socket access and reduce local resource and configuration overhead.

**Non-Goals:**

- Production deployment, high availability, authentication, TLS, object storage, or production
  retention and sampling policy.
- Independent lifecycle, scaling, or version selection for each bundled backend.
- Automatic Docker stdout/stderr discovery, host telemetry, or application Prometheus scraping.
- Routing general operational traces to Langfuse or enabling prompt/completion capture by default.
- Adding application instrumentation before an application runtime exists in this repository.

## Decisions

### Use the pinned Grafana LGTM development image

Compose will run one `grafana/otel-lgtm` service, pinned by release tag and immutable digest. It
bundles Grafana, Loki, Tempo, Prometheus, Pyroscope, and an OpenTelemetry Collector with one
filesystem-backed `/data` volume. Pyroscope remains an unused image capability; no profiling
endpoint is part of this change.

This replaces the standalone Grafana, Loki, Tempo, Mimir, Collector, and Alloy services. Separate
services were initially chosen for inspectability and explicit Mimir support, but the user does
not require Mimir and the convenience image is a better match for local development. The bundled
processes share a failure and upgrade boundary, which is accepted here and explicitly unsuitable
for production.

### Replace the bundled Collector defaults with a mounted repository configuration

The service will mount a read-only config at `/otel-lgtm/otelcol-config.yaml`, the location the
image supports for overriding its bundled Collector. The run image still manages the Collector
process; the repository owns all receivers, processors, exporters, extensions, and pipelines that
form the privacy and routing contract.

The config exports through the backends' container-internal loopback endpoints:

| Signal | Bundled destination |
| --- | --- |
| traces | Tempo OTLP/HTTP on `127.0.0.1:4418` |
| metrics | Prometheus OTLP/HTTP on `127.0.0.1:9090/api/v1/otlp` |
| logs | Loki OTLP/HTTP on `127.0.0.1:3100/otlp` |

Direct application-to-backend export remains rejected because it exposes backend topology to
producers and cannot apply destination-specific content policy. Baking a custom Collector image
is no longer needed because the LGTM image explicitly supports the mounted config boundary.

### Keep separate operational and GenAI receivers

The Collector exposes two OTLP/gRPC and OTLP/HTTP receiver pairs:

| Receiver | Host ports | Destinations |
| --- | --- | --- |
| operational | `4317` / `4318` | traces → Tempo; metrics → Prometheus; logs → Loki |
| GenAI | `4327` / `4328` | redacted complete trace → Tempo; full complete trace → Langfuse |

Separate receivers are preferred to span filtering because a GenAI workflow includes root, HTTP,
retrieval, tool, and model spans; filtering only `gen_ai.*` leaves would fragment the trace.
Development uses no trace sampling. Pipelines add
`deployment.environment.name=development` only when absent, apply a memory limiter and batching,
and use independent exporter queues.

Both Tempo-bound trace pipelines remove secret-like transport attributes and these content keys:

```text
gen_ai.system_instructions
gen_ai.input.messages
gen_ai.output.messages
gen_ai.tool.definitions
gen_ai.tool.call.arguments
gen_ai.tool.call.result
```

Applying the same deletion to operational traces is defense-in-depth if a producer uses the wrong
receiver. The Langfuse pipeline removes transport secrets but deliberately keeps captured content.
It exports with HTTP Basic authentication from `LANGFUSE_AUTH_STRING` and
`x-langfuse-ingestion-version: "4"`. Missing or invalid credentials may fail that exporter but
must not block the operational pipelines.

Content capture remains opt-in at the producer. Attribute deletion is not a general sanitizer:
secrets embedded inside otherwise allowed values must be masked or allowlisted before export.

### Collect application logs only through OTLP

The operational receiver accepts OTLP logs and sends them to the bundled Loki. Resource
`service.name` and OTLP trace/span context provide identity and correlation without a Docker
socket. Alloy and automatic stdout discovery are removed. Applications that only write stdout
must add OTLP log instrumentation or an explicit external log collector later.

This reduces host access and moving parts, at the cost of not automatically collecting logs from
unmodified containers. That limitation is preferable for this repository because structured,
correlated application telemetry is the target.

### Use bundled Grafana provisioning and Prometheus

The LGTM image's provisioned Loki, Tempo, and Prometheus data sources become the default
exploration surface. The implementation will verify their presence and supported correlation
links with the pinned image instead of maintaining duplicate provisioning files. Metrics use
Prometheus's native OTLP receiver; Mimir-specific configuration and remote write are removed.

The Collector's own metrics are collected with the image's established local scrape pattern and
sent to Prometheus. Collector health remains a process-readiness signal at `/ready`; backend
queries remain the evidence of successful delivery.

### Use one loopback-published service and one volume

Compose publishes Grafana, both OTLP receiver pairs, and Collector health only on `127.0.0.1`.
Backend query and ingestion ports remain internal to the LGTM container. One named volume mounts
at `/data` and persists all bundled state. Existing standalone observability volumes are not
deleted during migration, making rollback and manual cleanup possible.

The existing direct authenticated Langfuse OTLP/HTTP endpoint stays unchanged. Documentation
distinguishes it from the operational and GenAI Collector endpoints and explains their content
policies.

## Risks / Trade-offs

- **[Shared failure boundary]** One container hosts every operational component → treat it as a
  disposable development appliance and verify each internal API, not only container health.
- **[Bundled version coupling]** Backends cannot be upgraded independently → pin the LGTM tag and
  digest, validate the exact image, and upgrade the appliance deliberately.
- **[No stdout discovery]** Uninstrumented container logs are absent from Loki → document OTLP log
  instrumentation as the supported path.
- **[Wrong receiver]** A producer can send GenAI content to the operational endpoint → apply the
  Tempo redaction processor to both trace paths and document endpoint ownership.
- **[Langfuse authentication not configured]** GenAI fan-out can fail before project credentials
  exist → keep operational delivery independent and surface exporter errors.
- **[Key-based redaction]** Secrets inside allowed attributes survive → keep content capture
  opt-in and require producer-side masking or allowlisting.
- **[Local data growth]** The shared volume can grow during prolonged use → rely on development
  retention defaults and document the destructive reset.
- **[Readiness is not delivery]** Healthy processes can still reject telemetry → retain canaries
  and backend queries for every signal path.

## Migration Plan

1. Verify the requested LGTM release exists for the host architecture, record its immutable
   digest, and inspect its supported config mount, health check, and internal endpoints.
2. Revise the OpenSpec artifacts and contract tests for the single-service topology.
3. Add the mounted Collector config, replace the six Compose services with `lgtm`, and replace
   their image inputs and volumes with one LGTM pin and volume.
4. Remove the standalone Collector build and superseded Grafana, Loki, Tempo, Mimir, and Alloy
   configuration files.
5. Stop and remove only the superseded observability containers, preserving their named volumes
   and every Langfuse service and volume. Start the LGTM service.
6. Verify internal readiness, provisioned data sources, missing-credential isolation, trace
   redaction and fan-out, Prometheus metrics including Collector self-metrics, OTLP logs in Loki,
   correlation, restart persistence, and strict OpenSpec validation.
7. Update the README with the exact commands and limitations that passed.

Rollback is non-destructive: stop the LGTM service, restore the prior Compose definitions and
configuration from version control, and restart the standalone services. Their old named volumes
remain available unless the developer explicitly removes them.
