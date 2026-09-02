import { AgentUnavailable } from "@/features/investigations/agent-unavailable";
import { CaseLauncher } from "@/features/investigations/case-launcher";
import { fetchAgentAvailability, fetchThreadPage } from "@/server/agent-api";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const availability = await fetchAgentAvailability();
  if (availability === "unavailable") return <AgentUnavailable retryHref="/" />;
  const recentThreads = await fetchThreadPage().then((page) => page.items).catch(() => []);

  return (
    <main className="landing-shell">
      <section className="landing-card" aria-labelledby="landing-title">
        <span className="eyebrow">Deep Analyst</span>
        <h1 id="landing-title">Start an investigation</h1>
        <p className="landing-status"><span aria-hidden="true">✓</span> Investigation agent ready</p>
        <p className="landing-intro">Open a case workspace to investigate evidence, trace connections, and continue previous work.</p>
        <CaseLauncher recentThreads={recentThreads} />
      </section>
    </main>
  );
}
