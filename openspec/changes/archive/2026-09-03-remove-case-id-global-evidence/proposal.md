## Why

Deep Analyst currently treats a caller-supplied `case_id` as both a data namespace and an unbypassable access filter. This can make a healthy evidence store appear empty and prevents the agent from selecting relevant evidence across the full corpus, which conflicts with the product model of fresh, independent conversations over one global evidence store.

## What Changes

- **BREAKING** Remove `case_id` from every raw and canonical evidence schema, generated identifier, database key, index, view, and stored relationship.
- **BREAKING** Remove caller-supplied case scope from investigation requests, runtime context, checkpoints, thread summaries, history, tools, grounding, and citations.
- Make structured queries, hybrid retrieval, record resolution, and graph traversal operate over the complete evidence store; the agent determines relevance through its prompts and tool inputs.
- Replace composite case-qualified identities with globally unique, source-qualified record, entity, relationship, account, communication, and transaction identities.
- **BREAKING** Replace the case launcher and `/cases/...` frontend routes with a conversation home, `New conversation`, recent conversations, and `/threads/<thread_id>` history routes.
- Regenerate the synthetic dataset, both language editions, manifests, expected previews, provenance, and ground truth without `case_id`.
- Migrate or explicitly rebuild local persisted evidence and checkpoint state so no active data or serialized conversation retains `case_id`.
- Remove active documentation and requirements that describe case-scoped or cross-case-denial behavior.

## Capabilities

### New Capabilities

- `investigation-chat-ui`: Provide a conversation-only frontend over the global evidence store, superseding the case-scoped UI requirements in the active `add-investigation-chat-frontend` change.

### Modified Capabilities

- `trg-synth-dataset`: Generate raw fixtures, stable identifiers, manifests, provenance, and ground truth without a case namespace.
- `ingestion-pipeline`: Ingest and upsert globally identified evidence without case-qualified keys or processing scope.
- `evidence-store`: Store and expose a global corpus without case columns, session scope, or case-filtered views.
- `investigation-tools`: Search, query, resolve, and traverse all evidence without trusted case context or cross-case rejection.
- `investigation-agent`: Ground answers against globally available evidence and remove case binding from durable agent state.
- `investigation-api`: Accept and return conversation identifiers without a caller-supplied case identifier.
- `conversation-history`: Persist independent threads without binding them to a case.

## Impact

- Affects the dataset generator and checked-in EN/EL editions, evidence models, ingestion service, PostgreSQL schema and initialization, investigation-agent domain and adapters, checkpoint schema, HTTP/SSE contracts, Next.js routes and components, tests, fixtures, documentation, and OpenSpec requirements.
- Changes public request/response shapes and frontend URLs; callers using `case_id` or `/cases/...` must migrate.
- Existing database rows and checkpoints require an explicit migration or a destructive local rebuild. Implementation must not delete local state without separate user approval.
- The active `add-investigation-chat-frontend` change contains conflicting case-scoped requirements and must be reconciled before implementation is considered complete.
- No new runtime dependency is required.
