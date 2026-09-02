export const ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

export type TurnStatus = "running" | "interrupted" | "completed" | "failed";
export type HistoryRole = "user" | "assistant";

export interface FieldLocator {
  kind: "field";
  field: string;
}

export interface TextSpanLocator {
  kind: "text_span";
  field: string;
  char_start: number;
  char_end: number;
  quote: string;
}

export interface SourceRef {
  record_id: string;
  locator: FieldLocator | TextSpanLocator;
}

export interface Citation {
  evidence_id: string;
  content_hash: string;
  source_ref: SourceRef;
}

export interface ThreadSummary {
  thread_id: string;
  case_id: string;
  turn_id: string;
  status: TurnStatus;
  created_at: string;
}

export interface ThreadPage {
  items: ThreadSummary[];
  next_cursor: string | null;
}

export interface MessageItem {
  message_id: string;
  sequence: number;
  turn_id: string;
  request_id: string;
  role: HistoryRole;
  content: string;
  citations: Citation[];
  turn_status: TurnStatus;
  created_at: string;
}

export interface MessagePage {
  items: MessageItem[];
  next_cursor: string | null;
}

export interface ProblemDetails {
  schema_version: 1;
  type: string;
  title: string;
  status: number;
  code: string;
  detail: string;
  retryable: boolean;
}

export interface InvokeRequest {
  request_id: string;
  thread_id: string;
  case_id: string;
  message: string;
}

export type ProgressPhase =
  | "checking_scope"
  | "updating_context"
  | "planning"
  | "searching_evidence"
  | "querying_records"
  | "finding_connections"
  | "verifying_answer"
  | "committing_answer";

export type PublicTool = "search_evidence" | "query_records" | "find_connections";

interface EnvelopeBase {
  schema_version: 1;
  thread_id: string;
  turn_id: string;
  timestamp: string;
}

export interface RunStartedEvent extends EnvelopeBase {
  event: "run.started";
  data: { status: "running" };
}

export interface ProgressEvent extends EnvelopeBase {
  event: "progress";
  data: {
    phase: ProgressPhase;
    tool: PublicTool | null;
    attempt: number | null;
    count: number | null;
  };
}

export interface AnswerDeltaEvent extends EnvelopeBase {
  event: "answer.delta";
  data: { index: number; text: string };
}

export interface RunCompletedEvent extends EnvelopeBase {
  event: "run.completed";
  data: { message_id: string; citations: Citation[]; status: "completed" };
}

export interface RunFailedEvent extends EnvelopeBase {
  event: "run.failed";
  data: { code: string; message: string; retryable: boolean };
}

export type InvestigationEvent =
  | RunStartedEvent
  | ProgressEvent
  | AnswerDeltaEvent
  | RunCompletedEvent
  | RunFailedEvent;

type JsonObject = Record<string, unknown>;

const TURN_STATUSES = new Set<TurnStatus>(["running", "interrupted", "completed", "failed"]);
const ROLES = new Set<HistoryRole>(["user", "assistant"]);
const PHASES = new Set<ProgressPhase>([
  "checking_scope",
  "updating_context",
  "planning",
  "searching_evidence",
  "querying_records",
  "finding_connections",
  "verifying_answer",
  "committing_answer",
]);
const TOOLS = new Set<PublicTool>(["search_evidence", "query_records", "find_connections"]);

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOnlyKeys(value: JsonObject, keys: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function isString(value: unknown, min = 1, max = Number.MAX_SAFE_INTEGER): value is string {
  return typeof value === "string" && value.length >= min && value.length <= max;
}

function isId(value: unknown): value is string {
  return isString(value, 1, 128) && ID_PATTERN.test(value);
}

function isTimestamp(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /(?:Z|[+-]\d{2}:\d{2})$/.test(value) &&
    Number.isFinite(Date.parse(value))
  );
}

function isNullableInteger(value: unknown, minimum: number): value is number | null {
  return value === null || (Number.isInteger(value) && (value as number) >= minimum);
}

function isLocator(value: unknown): value is FieldLocator | TextSpanLocator {
  if (!isObject(value) || !isString(value.field)) return false;
  if (value.kind === "field") return hasOnlyKeys(value, ["kind", "field"]);
  if (value.kind !== "text_span") return false;
  return (
    hasOnlyKeys(value, ["kind", "field", "char_start", "char_end", "quote"]) &&
    Number.isInteger(value.char_start) &&
    Number.isInteger(value.char_end) &&
    (value.char_start as number) >= 0 &&
    (value.char_end as number) > (value.char_start as number) &&
    typeof value.quote === "string" &&
    value.quote.length === (value.char_end as number) - (value.char_start as number)
  );
}

export function isCitation(value: unknown): value is Citation {
  if (!isObject(value) || !hasOnlyKeys(value, ["evidence_id", "content_hash", "source_ref"])) {
    return false;
  }
  return (
    isString(value.evidence_id, 1, 256) &&
    typeof value.content_hash === "string" &&
    /^[0-9a-f]{64}$/.test(value.content_hash) &&
    isObject(value.source_ref) &&
    hasOnlyKeys(value.source_ref, ["record_id", "locator"]) &&
    isString(value.source_ref.record_id) &&
    isLocator(value.source_ref.locator)
  );
}

function isCitationList(value: unknown): value is Citation[] {
  return Array.isArray(value) && value.length <= 256 && value.every(isCitation);
}

export function isThreadPage(value: unknown): value is ThreadPage {
  if (!isObject(value) || !hasOnlyKeys(value, ["items", "next_cursor"])) return false;
  return (
    Array.isArray(value.items) &&
    value.items.every(isThreadSummary) &&
    (value.next_cursor === null || isString(value.next_cursor, 1, 2_048))
  );
}

function isThreadSummary(value: unknown): value is ThreadSummary {
  if (
    !isObject(value) ||
    !hasOnlyKeys(value, ["thread_id", "case_id", "turn_id", "status", "created_at"])
  ) {
    return false;
  }
  return (
    isId(value.thread_id) &&
    isId(value.case_id) &&
    isId(value.turn_id) &&
    TURN_STATUSES.has(value.status as TurnStatus) &&
    isTimestamp(value.created_at)
  );
}

export function isMessagePage(value: unknown): value is MessagePage {
  if (!isObject(value) || !hasOnlyKeys(value, ["items", "next_cursor"])) return false;
  return (
    Array.isArray(value.items) &&
    value.items.every(isMessageItem) &&
    (value.next_cursor === null || isString(value.next_cursor, 1, 2_048))
  );
}

function isMessageItem(value: unknown): value is MessageItem {
  if (
    !isObject(value) ||
    !hasOnlyKeys(value, [
      "message_id",
      "sequence",
      "turn_id",
      "request_id",
      "role",
      "content",
      "citations",
      "turn_status",
      "created_at",
    ])
  ) {
    return false;
  }
  return (
    isString(value.message_id, 1, 128) &&
    Number.isInteger(value.sequence) &&
    (value.sequence as number) >= 1 &&
    isString(value.turn_id, 1, 128) &&
    isString(value.request_id, 1, 128) &&
    ROLES.has(value.role as HistoryRole) &&
    isString(value.content, 1, 128_000) &&
    isCitationList(value.citations) &&
    TURN_STATUSES.has(value.turn_status as TurnStatus) &&
    isTimestamp(value.created_at)
  );
}

export function isProblemDetails(value: unknown): value is ProblemDetails {
  if (
    !isObject(value) ||
    !hasOnlyKeys(value, [
      "schema_version",
      "type",
      "title",
      "status",
      "code",
      "detail",
      "retryable",
    ])
  ) {
    return false;
  }
  return (
    value.schema_version === 1 &&
    isString(value.type) &&
    isString(value.title) &&
    Number.isInteger(value.status) &&
    (value.status as number) >= 400 &&
    (value.status as number) <= 599 &&
    isString(value.code, 1, 64) &&
    isString(value.detail, 1, 512) &&
    typeof value.retryable === "boolean"
  );
}

function hasEnvelopeBase(value: JsonObject): boolean {
  return (
    value.schema_version === 1 &&
    isId(value.thread_id) &&
    isString(value.turn_id, 1, 128) &&
    isTimestamp(value.timestamp) &&
    isObject(value.data)
  );
}

export function parseInvestigationEvent(value: unknown): InvestigationEvent {
  if (
    !isObject(value) ||
    !hasOnlyKeys(value, ["schema_version", "event", "thread_id", "turn_id", "timestamp", "data"]) ||
    !hasEnvelopeBase(value)
  ) {
    throw new Error("Invalid investigation event envelope");
  }
  const data = value.data as JsonObject;
  if (value.event === "run.started" && hasOnlyKeys(data, ["status"]) && data.status === "running") {
    return value as unknown as RunStartedEvent;
  }
  if (
    value.event === "progress" &&
    hasOnlyKeys(data, ["phase", "tool", "attempt", "count"]) &&
    PHASES.has(data.phase as ProgressPhase) &&
    (data.tool === null || TOOLS.has(data.tool as PublicTool)) &&
    isNullableInteger(data.attempt, 1) &&
    isNullableInteger(data.count, 0)
  ) {
    return value as unknown as ProgressEvent;
  }
  if (
    value.event === "answer.delta" &&
    hasOnlyKeys(data, ["index", "text"]) &&
    Number.isInteger(data.index) &&
    (data.index as number) >= 0 &&
    isString(data.text, 1, 16_384)
  ) {
    return value as unknown as AnswerDeltaEvent;
  }
  if (
    value.event === "run.completed" &&
    hasOnlyKeys(data, ["message_id", "citations", "status"]) &&
    isString(data.message_id, 1, 128) &&
    isCitationList(data.citations) &&
    data.status === "completed"
  ) {
    return value as unknown as RunCompletedEvent;
  }
  if (
    value.event === "run.failed" &&
    hasOnlyKeys(data, ["code", "message", "retryable"]) &&
    isString(data.code, 1, 64) &&
    isString(data.message, 1, 512) &&
    typeof data.retryable === "boolean"
  ) {
    return value as unknown as RunFailedEvent;
  }
  throw new Error("Invalid investigation event data");
}
