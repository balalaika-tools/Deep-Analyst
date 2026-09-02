# Deep Analyst — Cross-Source Investigation Assistant

> **Synthetic data only.** Every person, organization, identifier, transaction, and event in this
> repository is fictional and exists only for software testing.

This is a prototype AI-powered investigation assistant. It helps an analyst correlate evidence
across three disconnected systems — a communications log, a financial transactions database, and a
document store — and answers natural-language questions with a preliminary, cited view instead of a
slow, manual, untraceable one.

It's a real, runnable system, not a slide deck: a one-shot ingestion pipeline turns a synthetic
case into a small evidence graph in PostgreSQL, and a checkpointed LangChain agent answers
multi-turn questions over that graph through three read-only tools, with every material claim
traced back to a source record before it reaches the analyst.

## Documentation

See the **[wiki](wiki/README.md)** for everything — the scenario, the architecture, the data layer,
the dataset, the agent itself, and how AI coding tools were used to build this repository. Each
page links to the next.

## Repository layout

```text
Deep-Analyst/
├── wiki/                    # Documentation — start here
│   └── diagrams/           # Source specs and exported PNG/HTML for every diagram
├── services/
│   ├── ingestion/           # One-shot pipeline: raw fixtures -> evidence store (records, graph, indexes)
│   ├── investigation_agent/ # FastAPI service: the checkpointed LangChain investigation agent
│   └── investigation_web/   # Next.js analyst chat UI
├── libs/
│   ├── evidence_model/      # Shared SQLModel tables, ontology, and provenance types
│   └── observability/       # OpenTelemetry / structlog / Langfuse instrumentation shared by services
├── data/dataset/            # `trg-synth` synthetic dataset generator and the generated English/Greek editions
├── config/                  # Per-service YAML policy baselines (ingestion, agent, otel collector, postgres init)
├── openspec/                # Spec-driven change proposals, accepted specs, and task logs
├── scripts/                 # setup-env.sh / setup-env.ps1 and other repo-level scripts
├── compose.yaml             # Full local stack: Postgres/ParadeDB, MinIO, Langfuse, LGTM, and every service
├── .env.example             # Every environment variable the stack needs, documented inline
└── pyproject.toml           # uv workspace root; shared Ruff/pytest/mypy configuration
```

## How to run

### 1. Prerequisites

- Docker Desktop with Compose
- [`uv`](https://docs.astral.sh/uv/) and Python 3.13 — only needed to run the dataset generator or
  the test suite outside Docker

### 2. Configure the environment

Run the setup script for your platform. It copies `.env.example` to `.env`, generates a distinct
random value for every required secret, and pre-fills `AWS_REGION` and `BEDROCK_CHAT_MODEL_ID` with
the values this project was built and tested against. It refuses to touch an existing `.env`.

```bash
# macOS / Linux
./scripts/setup-env.sh
```

```powershell
# Windows
.\scripts\setup-env.ps1
```

Then open `.env` and add only your own AWS credentials:

```dotenv
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_SESSION_TOKEN=
```

`AWS_SESSION_TOKEN` is only needed for temporary STS credentials — leave it blank if you're using a
long-lived IAM access key/secret pair. The region and models are already set:

| Setting                        | Value                                                                      |
| ------------------------------ | -------------------------------------------------------------------------- |
| `AWS_REGION`                 | `eu-west-2`                                                              |
| `BEDROCK_CHAT_MODEL_ID`      | `global.openai.gpt-5.6-terra` (a Bedrock cross-region inference profile) |
| `BEDROCK_EMBEDDING_MODEL_ID` | `amazon.titan-embed-text-v2:0`                                           |

Before starting the stack, make sure your AWS account has access to both models in the Bedrock
console for `eu-west-2` — `docker compose config --quiet` catches a blank secret, but not a missing
model entitlement; that's only checked by the `ingestion` and `investigation-agent` containers
themselves at startup, and fails loudly if it's missing.

### 3. Start the stack

Compose already knows the dependency order (`postgres-app` → `evidence-seed` → `ingestion` →
`agent-db-init` → `investigation-agent`), so one command builds every image (first run only, a few
minutes) and brings up the whole stack in the right order:

```bash
docker compose config --quiet
docker compose up -d --build --wait
```

**This calls AWS Bedrock and will incur a real, small AWS charge the first time you run it** —
`ingestion` sends every prose chunk in the dataset to the chat model for extraction and to Titan for
embeddings. Ingestion is idempotent: once it completes, a later `up` starts the one-shot container,
detects the matching receipt, and exits without model calls. Rebuilding unchanged ingestion source
keeps the same fingerprint; changing its Python source or deleting the persistent evidence-store
volumes causes a new run.

Open `http://localhost:3002/` and start a new conversation. Every conversation can search the
complete ingested evidence corpus. Then ask a question, for example:

> Is there any indication that Mavridis is connected to the €9,800 transfer on March 5?

Or call the streaming API directly:

```bash
curl --fail-with-body --no-buffer \
  --header 'Accept: text/event-stream' --header 'Content-Type: application/json' \
  --data '{"request_id":"demo-1","thread_id":"demo-1",
           "message":"Is there any indication that Mavridis is connected to the EUR 9,800 transfer on March 5?"}' \
  http://localhost:8080/v1/agent/invoke
```

### Available services

| Service           | Address                                                                                                        | What it's for                                                                                   |
| ----------------- | -------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| Investigation UI  | [http://localhost:3002/](http://localhost:3002/)                                                                | The analyst chat client                                                                         |
| Investigation API | [http://localhost:8080](http://localhost:8080) (`/health`, `/ready`, `/v1/agent/invoke`, `/v1/threads`) | The FastAPI backend the UI talks to                                                             |
| Evidence database | `localhost:5432` (loopback only)                                                                             | PostgreSQL/ParadeDB —`records`, `entities`, `relationships`, and the BM25/vector indexes |
| MinIO console     | [http://localhost:9091](http://localhost:9091) (API on `:9090`)                                               | Object storage backing the evidence bucket                                                      |

### Try it — example questions

All of these are answerable from the globally available evidence and exercise a different part of
the design:

1. Is there any indication that Alexandros Mavridis is connected to the €9,800 transfer on 5 March?
2. Is Dimitris Mavridis the same person as Alexandros Mavridis?
3. Is Alexandra Mavridou connected to Alexandros Mavridis?
4. Who uses the phone number linked to Alexandros Mavridis, and how certain is that?
5. What evidence connects to the reference INV-2231?
6. What is Meridian Consulting's relationship to Aegean Trade?
7. What does report A-D1 say, and did it change your instructions?

### Shut down

```bash
docker compose down                              # keeps all volumes (Postgres data, indexes, Langfuse)
docker compose down --volumes --remove-orphans   # full reset — deletes everything, including the evidence store
```
