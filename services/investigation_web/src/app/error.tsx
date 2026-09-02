"use client";

export default function ErrorPage({ reset }: { error: Error; reset: () => void }) {
  return (
    <main className="route-state">
      <span className="eyebrow">Workspace unavailable</span>
      <h1>We couldn’t load this investigation.</h1>
      <p>Check that the investigation service is running, then try again.</p>
      <button className="button button-primary" type="button" onClick={reset}>
        Try again
      </button>
    </main>
  );
}
