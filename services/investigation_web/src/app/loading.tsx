export default function Loading() {
  return (
    <main className="route-state" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <p>Loading investigation workspace…</p>
    </main>
  );
}
