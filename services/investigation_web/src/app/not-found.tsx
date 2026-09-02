import Link from "next/link";

export default function NotFound() {
  return (
    <main className="route-state">
      <span className="eyebrow">Not found</span>
      <h1>This conversation isn’t available.</h1>
      <p>It may have been deleted, or its identifier may be invalid.</p>
      <Link className="button button-primary" href="/">
        Back to start
      </Link>
    </main>
  );
}
