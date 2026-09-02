## MODIFIED Requirements

### Requirement: Common record envelope

Every source item SHALL become exactly one globally identified record carrying its source system, stable source record identifier, record type, UTC event time with the original timestamp retained, searchable text when the source has prose, a structured payload preserving source-specific fields and original lexemes, source path, and content hash. The envelope SHALL NOT carry or derive a case identifier.

#### Scenario: Record counts match the manifest

- **WHEN** a run completes against the English edition
- **THEN** the store holds 55 CDR, 18 device-extraction, 6 email, 18 account, 35 transaction, and 10 document records

#### Scenario: Original values survive normalization

- **WHEN** transaction `t_88` is ingested
- **THEN** its payload contains `amount_minor` 980000, currency `EUR`, the original amount lexeme, remittance reference `INV-2231`, and both UTC and original booking timestamps

#### Scenario: Document metadata is separated from body text

- **WHEN** a Markdown document with YAML front matter is ingested
- **THEN** supported front-matter fields are stored in the payload and record text contains only the document body

## ADDED Requirements

### Requirement: Global ingestion identity

The pipeline SHALL derive globally unique, deterministic record, entity, relationship, chunk, projection, and run identities from stable source-qualified inputs without a case component. Upserts and relationship resolution SHALL use those global identities and SHALL remain idempotent.

#### Scenario: Two source systems reuse a local identifier

- **WHEN** two source systems provide the same source-local identifier
- **THEN** ingestion creates distinct globally unique records by including source identity in the deterministic key

#### Scenario: The same edition is ingested twice

- **WHEN** the pipeline processes the same edition twice
- **THEN** record, entity, relationship, chunk, and projection counts remain unchanged

