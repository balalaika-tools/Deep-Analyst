interface AgentUnavailableProps {
  retryHref: string;
}

export function AgentUnavailable({ retryHref }: AgentUnavailableProps) {
  return (
    <main className="route-state route-state-unavailable">
      <section aria-labelledby="agent-unavailable-title" className="unavailable-card">
        <span className="eyebrow">Frontend online</span>
        <h1 id="agent-unavailable-title">Investigation backend unavailable</h1>
        <p>
          The web interface is running, but the agent did not become ready. Ingestion or database
          initialization may have failed.
        </p>

        <dl className="service-status" aria-label="Service status">
          <div>
            <dt>Web interface</dt>
            <dd className="status-ready"><span aria-hidden="true">✓</span> Operational</dd>
          </div>
          <div>
            <dt>Investigation agent</dt>
            <dd className="status-unavailable"><span aria-hidden="true">!</span> Unavailable</dd>
          </div>
        </dl>

        <div className="unavailable-actions">
          <a className="button button-primary" href={retryHref}>Try again</a>
          <a
            className="button button-secondary"
            href="http://localhost:3001/explore"
            rel="noreferrer"
            target="_blank"
          >
            Open Grafana logs
          </a>
        </div>

        <p className="operator-hint">
          In Grafana, select Loki and query <code>{'{service_name="ingestion"} |= "ingestion.run_failed"'}</code>.
        </p>
      </section>
    </main>
  );
}
