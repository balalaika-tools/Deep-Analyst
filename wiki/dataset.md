# Dataset

## Purpose

`trg-synth` is the deterministic synthetic dataset used to build and evaluate the investigation
assistant. It combines communications, bank activity, email, and investigation documents that only become
useful once evidence from several sources is connected — exactly the shape of problem the
[Data Layer](data-layer.md) and [Agent Layer](agent-layer.md) are built to handle.

Every person, organization, identifier, transaction, and event is fictional, including
format-valid phone numbers, IBANs, and BICs generated only to exercise validation code. This is a
software fixture, not a statistical model of real investigations.

## At a glance

| Property | Value |
|---|---|
| Identity | Global source-qualified IDs from canonical seed `20260305` |
| Canonical seed | `20260305` |
| Activity window | 20 February to 10 March 2026 |
| English edition | `trg-synth-en-v1.0.0` (the prototype's active edition) |
| Greek edition | `trg-synth-el-v1.0.0` (meaning-equivalent, held for future multilingual testing) |
| Records per edition | 142 |
| Core-story records | 44 |
| Background and hard-negative records | 98 |

## Evidence sources

| Source | Format | Records | Main content |
|---|---|---:|---|
| Carrier communications | `cdr.csv` | 55 | Call and SMS metadata; no message body |
| Device extraction | `extraction.jsonl` | 18 | SMS and app messages with device context |
| Email | `.eml` | 6 | Headers, subject, body, and source metadata |
| Accounts | `bank.sql` | 18 | IBAN, holder, BIC, and opening date |
| Transactions | `bank.sql` | 35 | Accounts, exact amount, time, status, and remittance |
| Case documents | Markdown | 10 | Surveillance, KYC, SAR, invoice, and background reports |

The formats deliberately stay different — the ingestion design has to normalize them, rather than
consume a fixture that arrives pre-joined.

## The core scenario

A bank Suspicious Activity Report describes three transfers by Aegean Trade, each kept just under
a reporting threshold. The structured transaction data separately includes a €9,800 transfer to
Meridian Consulting on 5 March. Other reports, communications, account records, an email, and an
invoice provide possible connections among Alexandros Mavridis, one or more phones, Katherine
Rossi, Aegean, Meridian, and the reference `INV-2231`.

No single record establishes the full story:

| Record | What it contributes | What it does not prove |
|---|---|---|
| `R-01` | Attributes a phone to Alexandros Mavridis and reports a possible Meridian association | That he authored every event from that phone, or controls Meridian |
| `c01` / `X-204` | Carrier metadata and extracted content for the same SMS | Who physically held the phone at that moment |
| `R-02` | Meridian's account and registered director | Criminal purpose, or Aegean's ownership |
| `t_88` | A booked €9,800 transfer referencing `INV-2231` | Why the payment occurred |

A useful system can combine these into a cited hypothesis. It must not turn that hypothesis into an
unsupported allegation.

## Built-in traps

The fixture is deliberately adversarial in a few specific, testable ways:

| Control | Expected behavior |
|---|---|
| Alexandros vs. Dimitris Mavridis | Keep separate — a shared surname and a family contact do not merge two people |
| Alexandra Mavridou | A near-name hard negative, not the same person |
| A phone used by more than one person | Keep phone identity separate from person identity |
| `INV-2231` vs. `INV-2237` | Preserve exact reference identity; do not fuzzy-match |
| `A-D1` | A document containing an embedded, instruction-like sentence — it must be treated as quoted source content, never followed as an instruction to the system |
| Amount-band background transactions | Do not infer suspicion from amount alone |

## Runtime evidence boundary

```text
data/dataset/editions/en/data/
├── raw/                  runtime evidence — the only directory ingestion may read
├── manifest.json         runtime integrity: source versions, counts, SHA-256 hashes
├── ground_truth.json     tests only — never ingested, indexed, or exposed to the agent
├── expected/             tests only
└── fixtures/quarantine/  malformed parser fixtures, tests only
```

Ingestion reads only one edition's `raw/` directory and `manifest.json`. The answer key and
expected outputs exist purely to evaluate the system from the outside; runtime code must never read
them while producing a result.

The English and Greek editions describe the same case, sharing stable source IDs, timestamps,
amounts, accounts, and intended evidence meaning — but translated text naturally changes bytes,
hashes, and character offsets, so the editions are ingested and evaluated separately. The prototype
indexes only the English edition.

## Generate and verify

From the repository root:

```bash
uv run --package dataset make-dataset --seed 20260305 --check
uv run pytest data/dataset/tests
```

See [`docs/DATASET_SPEC.md`](../docs/DATASET_SPEC.md) for the full reviewer-facing contract, and
[`data/dataset/README.md`](../data/dataset/README.md) for generation and maintenance commands.

Next → [Agent Layer](agent-layer.md)
