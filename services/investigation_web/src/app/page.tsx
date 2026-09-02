import { AgentUnavailable } from "@/features/investigations/agent-unavailable";
import { ConversationWorkspace } from "@/features/conversations/workspace";
import { fetchAgentAvailability, fetchThreadPage } from "@/server/agent-api";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const availability = await fetchAgentAvailability();
  if (availability === "unavailable") return <AgentUnavailable retryHref="/" />;
  const threads = await fetchThreadPage().catch(() => ({ items: [], next_cursor: null }));
  return (
    <ConversationWorkspace
      initialMessages={{ items: [], next_cursor: null }}
      initialThreads={threads}
      key="new-conversation"
      threadId={null}
    />
  );
}
