import { notFound } from "next/navigation";

import { ConversationWorkspace } from "@/features/conversations/workspace";
import { AgentUnavailable } from "@/features/investigations/agent-unavailable";
import { ID_PATTERN } from "@/features/investigations/contracts";
import { AgentApiError, fetchMessagePage, fetchThreadPage } from "@/server/agent-api";

export const dynamic = "force-dynamic";

export default async function ThreadPage(props: {
  params: Promise<{ caseId: string; threadId: string }>;
}) {
  const { caseId, threadId } = await props.params;
  if (!ID_PATTERN.test(caseId) || !ID_PATTERN.test(threadId)) notFound();

  let data;
  try {
    data = await Promise.all([fetchThreadPage(), fetchMessagePage(threadId)]);
  } catch (error) {
    if (error instanceof AgentApiError && error.status === 404) notFound();
    if (error instanceof AgentApiError) {
      return (
        <AgentUnavailable
          retryHref={`/cases/${encodeURIComponent(caseId)}/threads/${encodeURIComponent(threadId)}`}
        />
      );
    }
    throw error;
  }
  const [threads, messages] = data;
  return (
    <ConversationWorkspace
      caseId={caseId}
      initialMessages={messages}
      initialThreads={threads}
      threadId={threadId}
    />
  );
}
