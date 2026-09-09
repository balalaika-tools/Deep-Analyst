# Investigation Agent — practical guide

Continuation of [`EVIDENCE_GRAPH_GUIDE.md`](EVIDENCE_GRAPH_GUIDE.md): that guide covered
ingestion and how the graph gets built; this one covers **reading** that graph to answer
questions. All examples are real code from
`services/investigation_agent/src/investigation_agent/`. For the short conceptual
picture, see [`wiki/agent-layer.md`](../wiki/agent-layer.md) first.

## 0. The picture in one sentence

An **agent** (LangChain `create_agent`) answers each question using **3 read-only
tools**, only writes an answer after it passes a **grounding check** (proven against the
evidence it cited), and between turns it does not carry the whole conversation forward —
it keeps a **compact, checkpointed state** in the same Postgres.

```text
analyst question
   │
   ▼ guardrail (before it even reaches a tool/model)
   ▼ main agent (LangChain create_agent, model/tool loop)
        ├─ search_evidence  → nested sub-agent → BM25+vector search
        ├─ query_records    → nested sub-agent → SQL over views
        └─ find_connections → deterministic graph traversal (no model)
   ▼ AnswerDraft (private, with claims + evidence_ids)
   ▼ grounding check (deterministic + LLM entailment) — both must pass
   ▼ commit to checkpoint (Postgres) → SSE to the analyst
   ▼ turn-close: refreshes the working projection (memory) for the next turn
```

---

## 1. The main agent: how it's built

`genai/investigation/agent.py::build_investigation_agent()`:

```python
create_agent(
    model=components.model,
    tools=[search_evidence, query_records, find_connections],   # EXACTLY these 3
    state_schema=InvestigationAgentState,
    response_format=AnswerDraft,          # structured, not free text
    checkpointer=checkpointer,            # AsyncPostgresSaver — see §8.5
    middleware=[...],                     # 9 items, order below
)
```

The middleware order is **not** arbitrary — each item runs at a specific hook of the
LangChain loop (`before_agent` runs first, `after_model` runs **in reverse**):

| # | Middleware | Hook | What it does |
|---|---|---|---|
| 1 | `TurnIntakeMiddleware` | `before_agent` | Opens the turn once, writes the analyst's message to history |
| 2 | `InputGuardrailMiddleware` | `before_agent` | Guardrail before any tool is even built (§7) |
| 3 | `ModelFailureMiddleware` | `wrap_model_call` | Converts provider exceptions into a typed failure instead of raising |
| 4 | retry middleware | `wrap_model_call` | Backoff/jitter, only for transient model errors |
| 5 | `ContextMiddleware` | `wrap_model_call` | Rebuilds the system prompt from scratch every time (§8.2) |
| 6 | `EvidenceIndexMiddleware` | `wrap_tool_call` | Guards + merges each new piece of evidence into the index (§8.3) |
| 7 | `GroundingMiddleware` | `after_model` | 2-layer check before the answer is ever exposed (§7) |
| 8 | `ModelCallLimitMiddleware` / `ToolCallLimitMiddleware` | `after_model` | Hard call limits (run **before** grounding can jump back to "model") |
| 9 | `TurnCloseMiddleware` | `after_agent` | Closure fallback + memory refresh (§8.4) |

---

## 2. The 3 tools

All 3 share the same shape: `@tool(response_format="content_and_artifact")` — the
"content" is a small status string, the "artifact" is the actual `ToolOutcome`
(evidence + coverage + warnings) that the middleware in §8.3 picks up.

| Tool | What it calls | Who answers |
|---|---|---|
| `search_evidence` | `SearchEvidenceAgent.run()` | nested sub-agent (§3) |
| `query_records` | `QueryRecordsAgent.run()` | nested sub-agent (§3) |
| `find_connections` | `FindConnections.run()` (plain code) | deterministic, **no model** (§4) |

The system prompt tells the model explicitly: `find_connections` only accepts **exact
entity IDs** that have already appeared from another tool in this same turn — never a
name, never a made-up ID.

---

## 3. The two nested sub-agents

`search_evidence` and `query_records` are **also** `create_agent()` instances, but with
`checkpointer=None` — no corpus memory, no memory of previous turns, no visibility into
the main transcript. They're invoked fresh each time with a single `HumanMessage` (the
typed intent as JSON), nothing else.

| | `SearchEvidenceAgent` | `QueryRecordsAgent` |
|---|---|---|
| Its own tool | `retrieve(query, source_systems?, top_k≤20)` → BM25+vector | `execute_sql(sql, parameters?)` → SQL policy gate (§5) |
| Verdict schema | `SearchVerdict{status, selected_chunk_ids, safe_reason_code}` | `QueryVerdict{status, selected_row_ids, safe_reason_code}` |
| status ∈ | `sufficient / no_retrieved_support / retrieval_incomplete` | `query_sufficient / query_exhausted` |
| Can retry | up to **3 distinct** attempts (`nested_tool_call_limit≤3`) | same |

The exact same query/plan retried **verbatim** is rejected immediately
(`repeated_query_rejected`) — the budget of 3 is for *different* attempts, not retries.
If the nested agent exhausts its budget without a verdict, the parent simply sees it as
`retrieval_incomplete`/`query_exhausted` with a `nested_agent_limit_reached` warning —
**never** a raw exception surfacing to the main agent.

---

## 4. `find_connections`: deterministic graph traversal

Plain BFS from seed entities, hop by hop, no model involved. Two levels of limits — what
the model asks for, capped by what the server allows
(`min(requested, server)`, `db/connections.py::_effective_limits`):

| Limit | Default the model requests | Server ceiling (actually configured) |
|---|---|---|
| `max_depth` | 2 | **4** (`GRAPH_MAX_DEPTH`) |
| `max_paths` | 25 | **50** (`MAX_GRAPH_PATHS`) |
| `max_nodes` | 100 | **100** (`GRAPH_MAX_NODES`) |
| `max_edges` | 200 | **200** (`GRAPH_MAX_EDGES`) |

If something above the server ceiling is requested, it's silently capped at the ceiling
and a `graph_limits_capped` warning is added. Other filters: `statuses` (**default is
`confirmed` only** — `proposed` must be explicitly requested), `predicates`,
`target_entity_types`, time window. Any node/edge without a source is dropped before it
ever reaches the model.

**Important detail:** a node (entity) that's returned gets
`evidentiary_status = "verified"` (not `confirmed` — that value only exists for
relationships); a relationship keeps its actual status (`confirmed`/`proposed`); a whole
path becomes `proposed` if even one of its hops is proposed.

**Example** (continuing the scenario from the first guide): seed
`["PHONE:306971234567"]`. The only edge that touches a `PHONE` toward a `PERSON` is
`USES` (small ontology, [`EVIDENCE_GRAPH_GUIDE.md`](EVIDENCE_GRAPH_GUIDE.md)) and is
always `proposed`/`llm` — so whatever the traversal returns from this seed will be
**proposed**, not confirmed. There is no ontology-allowed edge going directly from
`PHONE` to `FINANCIAL_ACCOUNT` — that's deliberate (the ontology from the first guide),
not an implementation gap.

---

## 5. The SQL policy gate (behind `query_records`)

`db/record_query_policy/validation.py::validate_sql_plan()` does a full analysis of the
PostgreSQL AST (using `pglast`, not regex) **before** it ever touches the database:

1. Rejects SQL comments.
2. Must be a single `SELECT` statement.
3. Every AST node must be on an **allowlist** of ~30 node types (`SelectStmt,
   ColumnRef, FuncCall, JoinExpr, ...`) — an allowlist of the whole grammar, not a
   blocklist.
4. Every table must be either a CTE already defined in the same query, or a view in the
   `agent_read` schema — exactly 3: `transactions_v1`, `accounts_v1`,
   `communications_v1`. Never the real tables directly.
5. `SELECT *` is explicitly forbidden.
6. Functions are restricted to 14 (`abs, avg, coalesce, count, ...`), operators/casts are
   also allowlisted.
7. `require_top_level_provenance`: the top-level `SELECT` **must** return `record_id`,
   `content_hash`, **and** `source_refs` as columns — otherwise
   `"provenance_required"`. This is the mechanism that guarantees every returned row
   carries where it came from.

Every violation becomes a structured rejection message back to the nested query agent
(not a raw exception) — the model sees the exact problem and can fix it within its
budget of 3 attempts.

---

## 6. Guardrails: two different points

| Where | When it runs | What it checks |
|---|---|---|
| `InputGuardrailMiddleware` | `before_agent`, before anything is built | The analyst's raw question itself: prompt injection / off-topic. `indeterminate` = **failure**, not "ok" (fail-closed). If the guardrail model itself is unavailable → `guardrail_unavailable`, never a silent pass-through. |
| evidence-content guard (`EvidenceIndexMiddleware`) | every time a tool returns evidence | Every chunk/row/label is wrapped in `<untrusted-evidence>` or `<suspicious-untrusted-evidence>` before any model sees it (main, nested, projection, verifier) |

PII content is **not** censored from evidence/state — only telemetry (traces/logs) is
content-free by default.

---

## 7. Grounding: why an answer doesn't come out "by chance"

The model first produces a **private** `AnswerDraft`:

```python
class AnswerDraft:
    answer: str                       # markdown, ≤16,000 chars (max_answer_chars)
    claims: tuple[AnswerClaim, ...]   # ≤128
class AnswerClaim:
    claim_id: str
    text: str
    kind: VERIFIED | PROPOSED | HYPOTHESIS | LIMITATION
    material: bool = True
    evidence_ids: tuple[str, ...] = ()
    # a material, non-LIMITATION claim MUST have evidence_ids
```

Two levels of checking, **both** must pass before even a single character reaches the
analyst (`genai/investigation/grounding.py`):

**Level 1 — deterministic** (`deterministic_violations`, plain code):
- every cited `evidence_id` must exist in the evidence index;
- a claim with `kind=VERIFIED` **cannot** cite a card with `evidentiary_status ==
  "proposed"` (→ `unqualified_proposed`) — this is the mechanism that forces the
  `PHONE --USES--> PERSON` from §4 to be labeled `PROPOSED` in the answer, never
  `VERIFIED`;
- if this turn's coverage was incomplete **and** the answer contains absence phrasing
  ("does not exist", "never occurred"...) → `absence_claim_from_incomplete_coverage`
  — this is the code behind "a retrieval miss is never proof of absence."

**Level 2 — LLM entailment** (only if level 1 passes, and only for material claims): a
separate model call, sees the claim's text **and** the actual content of the evidence it
cited (not just IDs), returns an `EntailmentVerdict{claim_id, supported,
safe_reason_code}` per claim. A claim survives only if `supported=True` **and**
`safe_reason_code=="entailed"`.

**Repair**: exactly 1 repair attempt (`max_repairs=1`) — the draft goes back to the model
with the list of violations; a second failure ends the turn with
`safe_failure_code="grounding_failed"` — **nothing** reaches the analyst.

---

## 8. The memory implementation (the interesting part)

"Memory" is **not** the full conversation transcript. It's a validated, checkpointed
**state** made of **6 sections** (`domain/investigation_state.py`) — the wiki mentions 3
(control/evidence/projection), but in practice there are 6:

```python
class InvestigationState:
    control: ControlState        # policy version — rigid, the model never changes it
    turn: TurnState | None       # scratch space for THIS turn (repair_count, safe_failure_code...)
    evidence: EvidenceIndex      # bounded evidence cards (§8.3)
    projection: WorkingProjection # the "memory" across turns (§8.4)
    history: HistoryState        # the product transcript — NEVER goes to the model (§8.1)
    usage: UsageCounters         # cumulative counters (model_calls, tool_calls, rows...)
```

### 8.1 `history` vs `projection` — two different things

`history.messages`: every user/assistant message, with `citations` (evidence_id +
content_hash + source_ref) on assistant messages. This is the transcript the analyst
sees in the UI (via the history API) — **but it never enters the model's prompt** (see
§8.2, `build_system_prompt` never touches it at all).

`projection`: a **replaceable** (not append-only) summary — this IS what the model sees
as "what we know so far."

### 8.2 How the system prompt is built on *every* model call

`middleware/context.py::build_system_prompt()` — literally string concatenation of 4
sections, fresh every time:

```python
sections = [
    MAIN_SYSTEM_PROMPT,
    "Control (system-owned, not changeable): " + json.dumps({"policy_version": ...}),
    "Working projection from prior turns: " + json.dumps(projection.model_dump()),
    "Evidence index (cite these IDs...): " + "\n".join(one line per card, ≤240 chars each),
]
```

Then only the messages from **this** turn are appended (`trim_turn_messages`, trims from
oldest "steps" toward newest if `max_context_tokens × 4` chars is exceeded, always
re-closing an `<untrusted-evidence>` tag that got cut mid-way). No reference to
`history` at all. This is exactly the wiki's claim "control + projection + evidence
cards + current turn" — confirmed byte-for-byte in the code.

### 8.3 Evidence index: the "cards"

One card (`EvidenceCard`):

```python
EvidenceCard(
  evidence_id="chunk:docs:R-01#0-812", kind="chunk",
  content_hash="c9f2...", source_refs=[...],
  evidentiary_status="confirmed",  # or verified/proposed — §4
  tool="search_evidence", display="A. Mavridis... uses telephone +30 697 123 4567...",
  suspicious_content=False, guard_status="allowed",
  first_seen_turn_id="turn_ab12", sequence=1,
)
```

`upsert_evidence()` (`domain/investigation_state.py`): a card with an already-known
`evidence_id` is a no-op (no duplicate, no status upgrade). When
`max_evidence_cards` (**default 200**) is exceeded, the oldest cards that are **not**
referenced by the current projection and were **not** first seen in this turn are
dropped first; only if that's not enough does it start touching "protected" cards. A
`coverage_notice="evidence_index_bounded"` is set — the analyst/agent knows something
was trimmed, it doesn't vanish silently.

### 8.4 Working projection: the actual "memory across turns"

```python
WorkingProjection(
  source_turn_id="turn_ab12",
  user_goal="...", dialogue_summary="...",
  referent_bindings=[ReferentBinding(phrase="the account", target_id="FINANCIAL_ACCOUNT:GR36...", confidence="resolved")],
  focus_evidence_ids=(...), focus_entity_ids=(...),
  active_findings=[ProjectedFinding(finding_id="f1", statement="...", evidence_ids=(...))],
  hypotheses=[QualifiedHypothesis(..., qualification="proposed"|"uncertain")],
  open_questions=(...), next_steps=(...),
  projection_stale=False,
)
```

**How it's refreshed** (`genai/state_projection/compactor.py`, called from
`TurnCloseMiddleware`, **only** when `outcome=="completed"` — a refused/failed turn just
marks `projection_stale=True` without even calling a model):

1. Model input (`ProjectionInput`): the previous `projection` in full + this turn's
   question + **only** the evidence first seen in this turn (wrapped in
   `<untrusted-evidence>`) + the final answer. **Not** raw tool payloads, **not**
   message history.
2. The model must return a **whole** new `WorkingProjection` (not a diff).
3. `validate_projection()`: every evidence/entity ID it cites must already exist in the
   evidence index; a finding that cites a `proposed` card must contain the word
   "propos" inside its own statement (self-qualification); absence phrasing is forbidden
   if coverage was incomplete.
4. **Repair**: exactly 1 extra attempt, with the violations as feedback.
5. If it fails a second time: `stale_projection()` — keeps the **previous** valid
   projection, only marks `projection_stale=True`. Nothing is lost, the already-committed
   answer is unaffected.

**Important**: this is a *separate* repair budget from the grounding repair in §7 — two
independent "1 repair" mechanisms, not the same one.

### 8.5 How it lands in Postgres — what the "checkpointer" actually is

It's **not** a custom persistence layer — it's the **off-the-shelf** LangGraph
`AsyncPostgresSaver` (`db/checkpointer.py::create_checkpointer()`), writing to its own
standard checkpoint tables. The "custom" part is **the state schema** (the 6 sections
above), not the storage mechanism.

```text
agent_runtime schema (LangGraph checkpoint tables)  ← agent_writer role, CRUD only here
public schema (records/entities/relationships/...)  ← agent_reader role, SELECT only
agent_read schema (3 views)                          ← agent_reader role, SELECT only
```

Three separate, least-privilege pieces in the **same** Postgres — `agent_writer` cannot
touch evidence, `agent_reader` cannot touch checkpoints.

`durability="sync"` in `.astream(...)` means: every node (model, tool, hook) **blocks**
until it's fsynced to Postgres before the next node proceeds — if the process dies
between two nodes, a resume continues from the last checkpoint without re-running
anything that already completed.

### 8.6 Idempotency, resume, and the per-thread lock

`application/invoke_turn.py::InvokeTurn._resolve_action()` — 4 possible outcomes:

| Outcome | When |
|---|---|
| `NEW` | No state at all, or the thread has no matching turn |
| `RESUME` | The same `request_id` is already running (`RUNNING`) — reconnects, doesn't resend the user message |
| `REPLAY_COMPLETED` / `REPLAY_FAILED` | This `request_id` has already run — replays the result **without** re-running anything |
| — | Same `request_id`, **different** message → `IdempotencyConflict` |

`application/thread_locks.py::ThreadLockRegistry` — an **in-process** lock, not
distributed:

```python
class ThreadLockRegistry:
    _locks: dict[str, asyncio.Lock]
    _active_requests: dict[str, str]   # thread_id -> request_id
```

Two concurrent requests on the same thread: the same `request_id` gets
`RequestInProgress` (retry), a different `request_id` gets `ThreadBusy`. This is the
known "production boundary" — across multiple replicas, each would keep its **own**
dictionary, so it wouldn't prevent concurrent processing of the same thread on a
different replica.

### 8.7 Example of two turns in sequence

**Turn 1** — "Who uses +30 697 123 4567 and what's their connection to Meridian?"

```
InputGuardrailMiddleware  → allowed
search_evidence("phone 697 123 4567 Meridian")
   → nested agent finds chunk docs:R-01#0-812 → SearchVerdict(status=sufficient)
   → EvidenceIndexMiddleware: new card evidence_id="chunk:docs:R-01#0-812", sequence=1
find_connections(seed_entity_ids=["PHONE:306971234567"])
   → finds PHONE --USES(proposed)--> PERSON:docs:R-01:alexandros-mavridis
   → new card evidence_id="PERSON:docs:R-01:alexandros-mavridis", evidentiary_status=verified, sequence=2
   → new card evidence_id="rel:...", evidentiary_status=proposed, sequence=3
AnswerDraft: claim kind=PROPOSED "R-01 links the phone number to Alexandros Mavridis"
             (cannot be VERIFIED — the card is proposed, §7 would reject it)
grounding: 2/2 pass → commit → SSE answer.delta... → run.completed
TurnCloseMiddleware: outcome=completed → compactor builds a new WorkingProjection:
   focus_entity_ids=("PHONE:306971234567", "PERSON:docs:R-01:alexandros-mavridis")
   open_questions=("Is there a direct link between Mavridis and a Meridian account?",)
```

**Turn 2** — "And the Meridian account, who holds it?"

```
ContextMiddleware: the system prompt NOW contains Turn 1's WorkingProjection
   (not Turn 1's raw messages — those have been cleared)
query_records("SELECT record_id, content_hash, source_refs, holder_name, iban
                FROM agent_read.accounts_v1 WHERE holder_name ILIKE '%Meridian%'")
   → SQL policy gate: view allowed, columns known, provenance columns present → OK
   → returns bank:acct_mc, holder_name="Meridian Consulting Ltd"
AnswerDraft: claim kind=VERIFIED (the card is confirmed/deterministic, §HELD_BY in the first guide)
```

Turn 2 **never** re-read R-01's text or Turn 1's tool calls — only the previous
`projection` (a few hundred tokens) plus the already-indexed cards.

---

## 9. Actual limits (defaults, `config/settings.py`)

| Limit | Default | What |
|---|---|---|
| `main_model_call_limit` / `main_tool_call_limit` | 20 / 12 | Main agent, per turn |
| `closure_model_calls` | 1 | Reserved for the closure fallback — `loop_model_calls = main_model_call_limit - closure_model_calls` |
| `nested_model_call_limit` / `nested_tool_call_limit` | 6 / **≤3** | Each sub-agent |
| `max_evidence_cards` | 200 | Evidence index cap |
| `max_context_tokens` | 32,000 | × 4 chars/token = char budget for the prompt |
| `max_answer_chars` | 16,000 | Max length of the final answer |
| `turn_timeout_s` / `model_timeout_s` / `tool_timeout_s` | 120 / 45 / 30 | Wall-clock limits |
| `max_history_turns` | 50 | Full thread |
| `graph_max_depth/nodes/edges` / `max_graph_paths` | 4 / 100 / 200 / 50 | Server ceiling for `find_connections` |
| `max_retrieved_rows` / `max_query_rows` / `retrieval_top_k` | 256 / 100 / 20 | SQL + hybrid search |

When a limit is exhausted without an accepted answer, `TurnCloseMiddleware` tries
**one** last "closure" call (over already-indexed evidence only, no tools) before
declaring a `safe_failure_code`.

---

## 10. Where to look next

| Topic | File |
|---|---|
| Main agent, middleware order | `genai/investigation/agent.py` |
| `AnswerDraft` / claim schema | `genai/investigation/schemas.py` |
| The 3 tools | `genai/investigation/tools/{find_connections,query_records,search_evidence}.py` |
| Nested sub-agents | `genai/evidence_search/agent.py`, `genai/record_query/agent.py` |
| Deterministic graph traversal | `application/find_connections.py`, `domain/connections.py` |
| SQL policy gate | `db/record_query_policy/{catalog.py,validation.py}` |
| Grounding | `genai/investigation/grounding.py`, `middleware/grounding.py` |
| Guardrails | `genai/guardrails/{middleware.py,schemas.py}` |
| **State schema (6 sections)** | `domain/investigation_state.py` |
| **Working projection / compaction** | `genai/state_projection/{compactor.py,schemas.py}` |
| **Prompt assembly, every call** | `genai/investigation/middleware/context.py` |
| **Evidence merge + guard** | `genai/investigation/middleware/evidence.py` |
| **Checkpointer (LangGraph, Postgres)** | `db/checkpointer.py` |
| Idempotency / resume | `application/invoke_turn.py` |
| In-process thread lock | `application/thread_locks.py` |
| All limits | `config/settings.py` |
| SSE events / progress phases | `api/sse.py` |
| Conceptual picture (no code) | `wiki/agent-layer.md`, `wiki/architecture.md` |
| Machine-facing contract | `openspec/specs/investigation-agent/spec.md` |
