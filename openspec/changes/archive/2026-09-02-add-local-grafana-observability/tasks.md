## 1. Reproducible LGTM input

- [x] 1.1 Verify `grafana/otel-lgtm:0.30.2` exists for the Docker host architecture, inspect its supported Collector override and health interfaces, record its multi-architecture digest, and verify the immutable reference resolves.
- [x] 1.2 Replace the standalone observability image inputs in `.env.example` and the active ignored `.env` with the LGTM version and digest plus a blank documented `LANGFUSE_AUTH_STRING`, then verify rendered Compose images contain no `latest` or unset observability image input.

## 2. Collector configuration and contracts

- [x] 2.1 Add the read-only LGTM Collector override with operational and GenAI OTLP receiver pairs, Tempo/Prometheus/Loki routing, Collector self-metrics, health, batching, memory limiting, environment insertion, secret deletion, and authenticated full-content Langfuse export; verify the exact pinned image accepts the configuration.
- [x] 2.2 Adapt the focused Collector contract tests to the mounted LGTM configuration and verify receiver ownership, internal backend endpoints, all six Tempo deletion keys, Langfuse content preservation, the v4 header, and absence of trace sampling or individual-span GenAI filtering.
- [x] 2.3 Remove the superseded standalone Collector build and the Grafana, Loki, Tempo, Mimir, and Alloy configuration files, then verify no active Compose or documentation path references them.

## 3. Compose migration

- [x] 3.1 Replace the six standalone observability services in `compose.yaml` with one digest-pinned `lgtm` service, a read-only Collector config mount, one `/data` volume, Langfuse credential injection, health check, restart policy, and loopback-only Grafana, operational OTLP, GenAI OTLP, and health ports; verify `docker compose config --quiet` succeeds.
- [x] 3.2 Inspect the rendered Compose model and verify only one observability image and volume remain, no Docker socket is mounted, all published observability ports bind to `127.0.0.1`, and Langfuse services are unchanged.
- [x] 3.3 Stop and remove only the superseded observability containers while retaining their named volumes, start LGTM, and verify the container plus bundled Grafana, Loki, Tempo, Prometheus, and Collector readiness endpoints are stable.

## 4. Signal delivery and correlation

- [x] 4.1 Start LGTM without usable Langfuse authentication, send operational telemetry and a GenAI canary, and verify the Langfuse exporter failure is observable while operational delivery and the redacted Tempo path continue.
- [x] 4.2 Send a content-bearing trace through the operational receiver and verify the complete trace is queryable in Tempo, all six prohibited payload keys are absent, and no corresponding trace appears in Langfuse.
- [x] 4.3 Configure valid local Langfuse authentication, send the complete five-span GenAI canary through the GenAI receiver, and verify the same complete trace is redacted in Tempo but retains captured content in Langfuse.
- [x] 4.4 Send a canary OTLP metric through the operational receiver and verify it plus Collector self-metrics are queryable in Prometheus, including an exemplar that references a Tempo trace.
- [x] 4.5 Send a trace-correlated OTLP log through the operational receiver, verify it is queryable in Loki with its service and trace identity, and verify Grafana has provisioned Loki, Tempo, and Prometheus data sources with supported cross-signal links.
- [x] 4.6 Restart LGTM without removing its volume and verify previously ingested trace, metric, and log canaries remain queryable within the bundled backends' retention behavior.

## 5. Documentation and final validation

- [x] 5.1 Update `README.md` for the single LGTM service, Prometheus metrics, OTLP-only logs, endpoint ownership and content policy, credential setup, startup, verification, shutdown, persistence, limitations, retained legacy volumes, and destructive reset; verify every documented command matches the rendered Compose project.
- [x] 5.2 Run strict OpenSpec validation, Compose rendering, focused tests, style/type checks, exact-image Collector validation, and all end-to-end signal checks, then record successful commands and environment-only limitations in the implementation handoff.
