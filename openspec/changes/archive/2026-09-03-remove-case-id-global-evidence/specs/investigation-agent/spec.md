## MODIFIED Requirements

### Requirement: Compact checkpointed agent state

The agent state SHALL contain immutable schema and policy control, current-turn state, a bounded global evidence index, one replaceable working projection, and the product history transcript. It SHALL NOT contain or bind a case identity. Evidence cards SHALL be keyed by globally stable evidence identifiers and carry resolvable source references, content hashes, status, and bounded display fields. The framework working messages SHALL contain only the current turn and SHALL be cleared after the answer is committed.

#### Scenario: Model output attempts to introduce scope

- **WHEN** model output proposes a case, tenant, corpus, thread, schema-version, or policy override
- **THEN** it cannot change system-owned state or restrict the globally available evidence corpus

#### Scenario: Crash follows a completed tool

- **WHEN** a tool result has been synchronously checkpointed and execution stops before the next model call
- **THEN** resumption continues from that checkpoint without repeating the completed tool

#### Scenario: Crash occurs between tool execution and the next model call

- **WHEN** a tool result has been checkpointed but execution stops before the next model call
- **THEN** resuming continues from that checkpoint without repeating the completed tool

#### Scenario: Checkpoint write fails

- **WHEN** synchronous checkpoint persistence fails
- **THEN** the next node does not begin and no assistant message is committed from unpersisted state

#### Scenario: Model output cannot change trusted scope

- **WHEN** model output proposes a case, thread, schema-version, or policy override
- **THEN** it cannot introduce an evidence partition or change system-owned thread and policy state

#### Scenario: Evidence index reaches its bound

- **WHEN** more evidence cards are added than the configured bound permits
- **THEN** deterministic eviction preserves referenced cards and records an explicit coverage notice

### Requirement: Validated working projection refreshed at turn close

The working projection SHALL be replaced as a bounded whole at turn close and SHALL reference only globally resolvable evidence and entity identifiers. Projection validation SHALL reject invented identifiers, unsupported claims, and any attempted evidence-scope restriction. Projection failure SHALL preserve the last valid projection and committed answer.

#### Scenario: Projection references global evidence

- **WHEN** a candidate projection names evidence collected from any part of the evidence store
- **THEN** validation accepts it when its identifier and provenance resolve globally

#### Scenario: Turn close replaces the projection

- **WHEN** a turn commits an answer while closure budget remains
- **THEN** the hook validates and atomically replaces the projection using globally resolvable evidence

#### Scenario: Projection invents an evidence reference

- **WHEN** a candidate projection names an identifier absent from the evidence index
- **THEN** deterministic validation rejects it before it becomes durable state

#### Scenario: Projection remains usable after compactor failure

- **WHEN** bounded projection attempts fail
- **THEN** the last validated projection remains active, is marked stale, and the committed answer is preserved

### Requirement: Grounded finalization and two-phase public release

The agent SHALL finalize answers only from evidence cards whose globally stable identifiers, content hashes, locators, and source references resolve against the evidence store. Material factual claims SHALL cite supporting evidence; retrieval misses SHALL remain qualified limitations. Candidate output SHALL remain private until grounding validation and durable commit succeed.

#### Scenario: Relevant global evidence supports a claim

- **WHEN** a material claim is supported by valid evidence found anywhere in the store
- **THEN** the final answer may state the claim with validated citations to that evidence

#### Scenario: Candidate answer has complete support

- **WHEN** every material claim resolves to valid globally available evidence
- **THEN** the answer can be committed and publicly released with validated citations

#### Scenario: Candidate answer invents a citation

- **WHEN** a candidate cites an unknown evidence identifier or invalid locator
- **THEN** deterministic grounding rejects or repairs the draft before release

#### Scenario: Search finds no relevant support

- **WHEN** bounded global search returns no relevant evidence
- **THEN** the answer reports a qualified coverage limitation rather than claiming factual absence

#### Scenario: Proposed graph relationship is included

- **WHEN** an answer relies on an LLM-derived proposed relationship
- **THEN** the answer labels the relationship as proposed and cites its resolvable supporting sources

#### Scenario: Evidence cannot be resolved

- **WHEN** a cited record, hash, or locator fails global provenance validation
- **THEN** the draft is rejected or repaired before any answer text is released

## ADDED Requirements

### Requirement: Global evidence availability

Every evidence-capable turn SHALL allow the agent's bounded tools to inspect the complete configured evidence store. Conversation identifiers, URL values, prior thread metadata, and model output SHALL NOT silently reduce the readable corpus.

#### Scenario: Fresh thread asks about existing evidence

- **WHEN** a fresh thread asks a question supported by ingested evidence
- **THEN** the tools can retrieve that evidence without any prior dataset or case selection
