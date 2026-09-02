import { proxyAgentRequest } from "@/server/proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  return proxyAgentRequest(request, "/v1/agent/invoke");
}
