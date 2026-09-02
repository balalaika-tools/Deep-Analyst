## Why

The local environment needs a reproducible, vendor-neutral OTLP path for correlated traces,
metrics, and logs without the operational weight of running and maintaining every Grafana
backend as a separate service. Developers also need GenAI traces to remain useful in Langfuse
while the copies stored in Tempo are stripped of sensitive prompt, response, and tool content.

## What Changes

- Add one pinned `grafana/otel-lgtm` service to the Docker Compose project. The image bundles
  Grafana, Tempo, Loki, Prometheus, and an OpenTelemetry Collector for local development.
- Mount a repository-owned Collector configuration into the bundled Collector. It exposes
  separate operational and GenAI OTLP receiver boundaries.
- Route operational traces to Tempo, metrics to Prometheus, and OTLP logs to Loki through the
  services bundled in the LGTM container.
- Fan out complete GenAI traces to a redacted Tempo path and a content-preserving, authenticated
  Langfuse path. Remove system instructions, model messages, tool definitions, tool arguments,
  and tool results from every Tempo-bound GenAI span.
- Retain the existing direct authenticated Langfuse OTLP/HTTP endpoint so applications can choose
  direct or Collector-mediated GenAI export. Content capture remains opt-in at the application
  boundary.
- Persist the bundled backends through one named volume and expose only loopback-bound host ports.
- Use the data sources supplied by the LGTM image and document startup, endpoints, verification,
  security boundaries, persistence, limitations, and reset behavior.
- Remove the superseded standalone Grafana, Loki, Tempo, Mimir, Collector, and Alloy services and
  their repository-owned backend configurations. Automatic Docker stdout discovery is no longer
  part of this capability; applications send structured logs through OTLP.

## Capabilities

### New Capabilities

- `local-observability`: Reproducible local collection, storage, correlation, and exploration of
  traces, metrics, and OTLP logs through the bundled LGTM stack, with privacy-aware GenAI routing
  to Tempo and Langfuse.

### Modified Capabilities

None. The existing `local-langfuse` capability and its direct authenticated ingestion boundary
remain intact. The new `local-observability` capability adds an optional Collector-mediated
Langfuse path without making the direct path unavailable.

## Impact

- `compose.yaml` gains one local LGTM service and one persistent volume on the existing Compose
  network, replacing six standalone observability services and their volumes.
- A checked-in custom Collector configuration replaces the standalone Collector build and the
  individual Grafana, Tempo, Loki, Mimir, and Alloy configurations.
- `.env.example` carries the pinned LGTM image version and digest plus the optional Langfuse
  exporter credential.
- `README.md`, observability contract tests, and the canary workflow use Prometheus rather than
  Mimir and describe OTLP log ingestion rather than Docker stdout discovery.
- The stack remains explicitly non-production and uses the single-container, filesystem-backed
  defaults provided by the LGTM development image.
