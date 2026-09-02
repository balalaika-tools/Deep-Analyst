## Purpose

Provide a dedicated, persistent, locally contained PostgreSQL instance for application
evidence data, separate from the Langfuse stack, together with the one-shot Compose wiring
that runs ingestion against it.

## ADDED Requirements

### Requirement: Dedicated application database
The local Compose project SHALL run an application PostgreSQL service distinct from the
Langfuse PostgreSQL service, with the `vector` and `pg_search` extensions available, a pinned
image digest, a bounded health check, and its own named volume.

#### Scenario: Extensions are available
- **WHEN** the application database is healthy
- **THEN** `CREATE EXTENSION IF NOT EXISTS vector` and `CREATE EXTENSION IF NOT EXISTS
  pg_search` both succeed

#### Scenario: Langfuse storage is untouched
- **WHEN** the application database is created
- **THEN** the Langfuse PostgreSQL service, its volume, and its database contain no
  application tables

### Requirement: Persistent local state with explicit reset
Evidence data SHALL survive ordinary Compose stop, removal, and restart. Deleting it SHALL
require the documented destructive volume reset.

#### Scenario: Restart preserves evidence
- **WHEN** the project is stopped without removing volumes and started again
- **THEN** previously ingested records remain queryable

### Requirement: Loopback-only developer access
The application database port SHALL be published only on the host loopback interface so
developers and integration tests can connect, and SHALL NOT be reachable through a
non-loopback host address.

#### Scenario: Host connection
- **WHEN** a developer connects to the published loopback port with the configured
  credentials
- **THEN** the connection succeeds

### Requirement: Externalized credentials and versions
The database password, the evidence bucket access key and secret, image versions and digests,
Bedrock model identifiers, AWS credential passthrough, and OTLP endpoints SHALL come from the
ignored local environment file with a committed example, and Compose SHALL fail configuration
resolution when a required secret is absent.

#### Scenario: Missing database password
- **WHEN** the application database password is blank
- **THEN** `docker compose config` fails naming the missing value

#### Scenario: Missing evidence bucket secret
- **WHEN** the evidence bucket secret key is blank
- **THEN** `docker compose config` fails naming the missing value

### Requirement: Seeded evidence bucket
Compose SHALL define a one-shot `evidence-seed` service, on a digest-pinned MinIO client
image, that starts after the MinIO service is healthy, creates the `evidence` bucket when
absent, creates a dedicated user and a policy scoped to that bucket from the configured
evidence access key, mirrors only `raw/` and `manifest.json` of the configured edition to
`datasets/<edition>/`, and exits 0. It SHALL be the only container that mounts the repository
dataset, and it SHALL mount only those two paths read-only.

#### Scenario: Seed uploads only raw evidence
- **WHEN** the seed completes
- **THEN** the bucket holds `datasets/<edition>/manifest.json` and every file below
  `datasets/<edition>/raw/`, and no object for `ground_truth.json`, `expected/`, or
  `fixtures/quarantine/`

#### Scenario: Evidence key cannot reach Langfuse storage
- **WHEN** the evidence access key lists buckets or reads the Langfuse bucket
- **THEN** the request is denied

#### Scenario: Seed is idempotent
- **WHEN** the project starts again with the bucket already seeded
- **THEN** the seed exits 0 and the bucket objects are unchanged

### Requirement: One-shot ingestion service
Compose SHALL define an ingestion service that starts only after the application database and
MinIO are healthy and the evidence seed has completed successfully, runs once, does not
restart on exit, mounts nothing from the repository except its read-only policy YAML, and
exits with status 0 on both a completed and a skipped run so dependents can wait on its
completion.

#### Scenario: Ingestion runs on first start
- **WHEN** the project starts with an empty application database
- **THEN** the ingestion container runs to completion, exits 0, and is not restarted

#### Scenario: Ingestion is skipped on later starts
- **WHEN** the project starts again with a matching receipt and completed run
- **THEN** the ingestion container exits 0 without reading sources or calling models

#### Scenario: Dependents can wait for completion
- **WHEN** a future service declares a dependency on successful ingestion completion
- **THEN** Compose starts it only after the ingestion container has exited 0
