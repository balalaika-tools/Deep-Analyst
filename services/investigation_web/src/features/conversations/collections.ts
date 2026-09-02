import type { MessageItem, ThreadSummary } from "@/features/investigations/contracts";

export function mergeThreads(
  current: readonly ThreadSummary[],
  incoming: readonly ThreadSummary[],
): ThreadSummary[] {
  const seen = new Set(current.map((thread) => thread.thread_id));
  return [...current, ...incoming.filter((thread) => !seen.has(thread.thread_id))];
}

export function mergeMessages(
  current: readonly MessageItem[],
  incoming: readonly MessageItem[],
): MessageItem[] {
  const byId = new Map(current.map((message) => [message.message_id, message]));
  for (const message of incoming) byId.set(message.message_id, message);
  return [...byId.values()].sort(
    (left, right) => left.sequence - right.sequence || left.message_id.localeCompare(right.message_id),
  );
}
