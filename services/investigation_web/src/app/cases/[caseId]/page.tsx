import { notFound } from "next/navigation";

import { ConversationWorkspace } from "@/features/conversations/workspace";
import { AgentUnavailable } from "@/features/investigations/agent-unavailable";
import { ID_PATTERN } from "@/features/investigations/contracts";
import { AgentApiError, fetchThreadPage } from "@/server/agent-api";

export const dynamic = "force-dynamic";

export default async function CasePage(props: { params: Promise<{ caseId: string }> }) {
  const { caseId } = await props.params;
  if (!ID_PATTERN.test(caseId)) notFound();
  let threads;
  try {
    threads = await fetchThreadPage();
  } catch (error) {
    if (error instanceof AgentApiError) {
      return <AgentUnavailable retryHref={`/cases/${encodeURIComponent(caseId)}`} />;
    }
    throw error;
  }
  return (
    <ConversationWorkspace
      caseId={caseId}
      initialMessages={{ items: [], next_cursor: null }}
      initialThreads={threads}
      threadId={null}
    />
  );
}
