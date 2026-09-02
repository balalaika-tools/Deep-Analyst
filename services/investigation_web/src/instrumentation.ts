export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    const { getAgentBaseUrl } = await import("@/server/environment");
    getAgentBaseUrl();
  }
}
