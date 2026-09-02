import { notFound } from "next/navigation";

import { ConversationWorkspace } from "@/features/conversations/workspace";
import { AgentUnavailable } from "@/features/investigations/agent-unavailable";
import { ID_PATTERN } from "@/features/investigations/contracts";
import { AgentApiError, fetchMessagePage, fetchThreadPage } from "@/server/agent-api";

export const dynamic = "force-dynamic";

export default async function ThreadPage(props: {
  params: Promise<{ threadId: string }>;
}) {
  const { threadId } = await props.params;
  if (!ID_PATTERN.test(threadId)) notFound();

  let data;
  try {
    data = await Promise.all([fetchThreadPage(), fetchMessagePage(threadId)]);
  } catch (error) {
    if (error instanceof AgentApiError && error.status === 404) notFound();
    if (error instanceof AgentApiError) {
      return <AgentUnavailable retryHref={`/threads/${encodeURIComponent(threadId)}`} />;
    }
    throw error;
  }
  const [threads, messages] = data;
  return (
    <ConversationWorkspace
      initialMessages={messages}
      initialThreads={threads}
      key={threadId}
      threadId={threadId}
    />
  );
}
