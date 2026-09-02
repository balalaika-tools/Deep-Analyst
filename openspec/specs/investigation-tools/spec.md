# Investigation Tools Specification

## Purpose

Define typed, bounded, globally scoped tool contracts for a hybrid retrieval sub-agent, a
policy-gated structured-query sub-agent, and deterministic traversal of sourced relationships.

## Requirements

### Requirement: Validated and bounded tool boundary
Each investigation tool SHALL accept and return a versioned validated contract. Model-authored
input SHALL be validated before database, embedding, or graph work. Tools SHALL operate over the
global evidence store and SHALL neither accept nor inherit a hidden partition. Results SHALL
identify status, tool-call identifier, consumed budget, warnings, and evidence references or a
safe failure code without exposing credentials, raw diagnostics, or stack traces.

#### Scenario: Invalid input performs no I/O
- **WHEN** a tool receives an argument that fails its Pydantic contract
- **THEN** it returns a bounded validation failure and performs no database, embedding, or model
  operation

#### Scenario: Investigation combines differently sourced evidence
- **WHEN** relevant evidence records come from different source collections
- **THEN** the tool can return all matching records in one bounded result

#### Scenario: Model-supplied scope is not authoritative
- **WHEN** model-authored tool input attempts to introduce hidden scope
- **THEN** strict validation rejects the value and no evidence partition is applied

### Requirement: Hybrid self-correcting evidence search
`search_evidence` SHALL invoke a nested, checkpointer-free LangChain `create_agent` whose only tool
is `retrieve`. On each `retrieve` call the tool SHALL fetch candidates from both BM25 lexical
search and vector similarity search across the complete evidence corpus using applicable source and time
filters, combine rankings with a configured deterministic fusion rule, deduplicate candidates by
stable chunk identifier, and return bounded chunk evidence carrying source references, content
hashes, modality scores, and attempt provenance as a delimited untrusted tool message.

The main agent SHALL pass a bounded typed `SearchIntent` containing the exact current question,
objective, stable hard and soft constraints, selected evidence identifiers, and chunk identifiers
already indexed for the thread. It SHALL NOT pass its own messages. The nested agent SHALL judge
relevance and sufficiency against that question, objective, and constraints. When evidence is
insufficient it MAY reformulate, but `retrieve` SHALL be limited to three calls per invocation by
tool-call limit middleware. `retrieve` SHALL exclude the parent-supplied seen set on every call
and every chunk returned earlier in the current invocation on later calls, and SHALL reject a
repeated normalized query fingerprint without I/O. The nested structured response SHALL be a
`SearchOutcome`; the tool SHALL accept only selected identifiers that were retrieved in this
invocation.

#### Scenario: Lexical and semantic evidence are fused deterministically
- **WHEN** BM25 and vector search return overlapping and non-overlapping chunks for one attempt
- **THEN** `retrieve` returns one deterministically ordered candidate per chunk identifier with
  both modality contributions preserved when present

#### Scenario: An insufficient first attempt is reformulated
- **WHEN** the nested agent judges the first attempt insufficient
- **THEN** it may issue a distinct retrieval query that excludes all chunks observed in the first
  attempt

#### Scenario: A later tool invocation excludes prior evidence
- **WHEN** the main agent invokes `search_evidence` again for the same investigation intent and
  supplies chunk identifiers already present in the evidence index
- **THEN** both retrieval modalities exclude those chunks from every attempt
- **AND** the nested agent cannot remove the parent-supplied exclusions

#### Scenario: Repeated retrieval plans do not execute
- **WHEN** the nested agent proposes a query whose normalized fingerprint was already attempted in
  the invocation
- **THEN** `retrieve` returns a typed rejection without I/O
- **AND** the invocation cannot exceed three executed retrievals

#### Scenario: Exhausted retrieval has an explicit meaning
- **WHEN** three distinct attempts do not produce sufficient relevant evidence
- **THEN** the tool returns `no_retrieved_support` or `retrieval_incomplete` with attempt summaries
  and SHALL NOT represent the retrieval miss as proof that the queried fact is false or absent

#### Scenario: Nested agent selects an unretrieved identifier
- **WHEN** the nested structured response names a chunk that no `retrieve` call in this invocation
  returned
- **THEN** the tool drops that identifier and records a warning

### Requirement: Agent-owned structured-record querying
`query_records` SHALL invoke a nested, checkpointer-free LangChain `create_agent` whose only tool
is `execute_sql`. The main agent SHALL pass a typed `QueryIntent` containing the current question,
query objective, hard and soft constraints, selected evidence identifiers, and desired result
shape. The tool SHALL bound this context and add a server-owned schema description of the
allowlisted `agent_read` views; it SHALL NOT pass database credentials, role names, unrestricted
schema metadata, or the main agent's messages to the nested agent.

The nested agent SHALL own SQL generation from its first attempt. `execute_sql` SHALL accept one
statement plus a typed parameter map, run the deterministic policy gate and the guarded executor,
and return either bounded rows or a safe classification of parse, policy, schema, execution, or
empty-result failure so the nested agent can revise. A plan fingerprint SHALL cover the canonical
parsed statement, typed bound parameters, and allowlist/schema version; a repeated fingerprint
SHALL be rejected without database I/O. `execute_sql` SHALL be limited to three calls per
invocation by tool-call limit middleware. The nested structured response SHALL be a
`QueryOutcome`; the tool SHALL accept only selected row identifiers that were returned in this
invocation, and SHALL return a typed exhausted result rather than starting an unbounded loop.

#### Scenario: First SQL is authored inside the nested agent
- **WHEN** the main agent invokes `query_records` with a valid `QueryIntent`
- **THEN** the nested agent authors and submits the first SQL plan using only the bounded intent
  and server-owned schema context

#### Scenario: A correctable failure is revised locally
- **WHEN** an attempted statement fails parsing, policy, schema validation, or execution, or
  returns no rows
- **THEN** the nested agent may produce a distinct corrected plan without the main agent
  reconstructing the local attempt history

#### Scenario: Query correction is bounded
- **WHEN** three distinct SQL plans fail or remain insufficient
- **THEN** `query_records` returns a typed exhausted result containing bounded attempt summaries to
  the main agent

#### Scenario: Repeated plan does not execute
- **WHEN** the nested agent submits a statement whose fingerprint was already executed in this
  invocation
- **THEN** `execute_sql` returns a typed rejection without a database round trip

### Requirement: Deterministic SQL policy enforcement
Every statement submitted to `execute_sql` SHALL pass a deterministic AST-based policy gate before
execution. The gate SHALL accept exactly one read-only `SELECT`, including a side-effect-free CTE,
and SHALL reject multiple statements, DML, DDL, `COPY`, `SET`, `set_config`, `current_setting`,
transaction control, row locking, `SELECT INTO`, temporary or unqualified relations, system
catalogs, comments or constructs used to hide additional statements, and unapproved functions.
Tables, views, columns, operators, and functions SHALL be restricted to schema-qualified entries in
a versioned allowlist under the `agent_read` surface; parse ambiguity or any unvisited AST node
SHALL fail closed.

All model-derived values SHALL be bound parameters rather than SQL interpolation. The executor
SHALL run the statement on the read-only reader pool inside a transaction and SHALL apply statement and lock timeouts, maximum rows,
maximum bytes, and a server-enforced outer result limit that the authored statement cannot weaken.
Only validated, bounded rows and provenance SHALL leave the executor.

#### Scenario: A safe parameterized select executes
- **WHEN** the nested agent proposes one parameterized `SELECT` over allowlisted `agent_read`
  columns within configured limits
- **THEN** the executor runs it read-only across the global evidence store and returns at most the
  configured row and byte limits

#### Scenario: An attempted copy is blocked
- **WHEN** the nested agent proposes `COPY`, a data-changing CTE, or any additional statement
- **THEN** the policy gate rejects the plan before a database round trip

#### Scenario: Select cannot create or shadow an object
- **WHEN** the nested agent proposes `SELECT INTO`, a `pg_temp` relation, or an unqualified
  relation
- **THEN** the policy gate rejects the plan before execution

#### Scenario: A query cannot weaken its resource limit
- **WHEN** the authored statement omits a limit or requests more than the configured maximum
- **THEN** the executor independently caps the returned rows and bytes and enforces its statement
  timeout

#### Scenario: Database diagnostics are safe to revise from
- **WHEN** execution fails with a database error that is eligible for semantic correction
- **THEN** the nested agent receives only a stable error class and bounded allowlisted detail,
  while raw SQL diagnostics and database identifiers remain internal

### Requirement: Bounded sourced graph traversal
`find_connections` SHALL execute deterministic, bounded traversal over all canonical entities and
relationships rather than delegating traversal decisions to another model. Its validated input
SHALL identify seed entity identifiers and explicit filters for relationship status, predicates,
and time, together with configured maximum depth, path count, and result rows. The executor SHALL
cap every requested bound at a server-owned maximum, prevent cyclic path expansion, and preserve
the distinction between `confirmed` and `proposed` relationships.

Every returned vertex and edge SHALL include its stable identifier and source references. A path
whose evidence cannot be resolved globally SHALL not be returned as a supported
connection.

#### Scenario: Traversal remains within bounds
- **WHEN** a seed entity belongs to a highly connected component
- **THEN** traversal stops at the effective depth, path, and row limits without model-directed
  expansion beyond them

#### Scenario: Proposed relationships remain qualified
- **WHEN** a returned path contains an LLM-derived `proposed` relationship
- **THEN** the result labels that edge as proposed and includes its supporting source references

#### Scenario: Global seed is resolvable
- **WHEN** a globally identified seed exists anywhere in the configured evidence store
- **THEN** traversal may resolve it without applying or revealing a legacy partition boundary

### Requirement: Tool retry ownership
All transient database, embedding, and provider calls made by investigation tools SHALL use bounded
retry middleware with configured exponential backoff and jitter. A transient retry SHALL repeat the
same physical operation and SHALL NOT consume a `retrieve` or `execute_sql` tool call. Validation,
policy, and other permanent failures SHALL not be retried; retrieval reformulation and SQL
correction SHALL remain owned by their respective nested agents.

#### Scenario: Transient query interruption preserves the semantic attempt
- **WHEN** an eligible connection interruption occurs while executing an approved SQL statement and
  the same statement succeeds on a bounded physical retry
- **THEN** `query_records` reports one semantic plan with two physical execution attempts

#### Scenario: Policy rejection is never transiently retried
- **WHEN** an SQL plan is rejected by the AST policy gate
- **THEN** tool retry middleware performs no database retry and returns the safe rejection to the
  nested agent's bounded loop
