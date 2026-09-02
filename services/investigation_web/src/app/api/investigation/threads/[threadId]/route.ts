import { proxyAgentRequest } from "@/server/proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function DELETE(
  request: Request,
  context: { params: Promise<{ threadId: string }> },
): Promise<Response> {
  const { threadId } = await context.params;
  return proxyAgentRequest(request, `/v1/threads/${encodeURIComponent(threadId)}`);
}
