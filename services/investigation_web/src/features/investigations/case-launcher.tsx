"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import type { ThreadSummary } from "./contracts";
import { ID_PATTERN } from "./contracts";

interface CaseLauncherProps {
  recentThreads: readonly ThreadSummary[];
}

function uniqueRecentCases(threads: readonly ThreadSummary[]): ThreadSummary[] {
  const seen = new Set<string>();
  return threads.filter((thread) => {
    if (seen.has(thread.case_id)) return false;
    seen.add(thread.case_id);
    return true;
  });
}

export function CaseLauncher({ recentThreads }: CaseLauncherProps) {
  const router = useRouter();
  const [caseId, setCaseId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const cases = uniqueRecentCases(recentThreads);

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const value = caseId.trim();
    if (!ID_PATTERN.test(value)) {
      setError("Use 1–128 letters, numbers, dots, underscores, colons, or hyphens.");
      return;
    }
    router.push(`/cases/${encodeURIComponent(value)}`);
  }

  return (
    <div className="case-launcher">
      <form className="case-form" onSubmit={submit} noValidate>
        <label htmlFor="case-id">Case ID</label>
        <div className="case-form-row">
          <input
            aria-describedby={error ? "case-id-error" : "case-id-help"}
            aria-invalid={Boolean(error)}
            autoComplete="off"
            autoFocus
            id="case-id"
            maxLength={128}
            onChange={(event) => {
              setCaseId(event.target.value);
              if (error) setError(null);
            }}
            placeholder="e.g. case-123"
            spellCheck={false}
            value={caseId}
          />
          <button className="button button-primary" type="submit">Open workspace</button>
        </div>
        {error ? <p className="field-error" id="case-id-error" role="alert">{error}</p> : (
          <p className="field-help" id="case-id-help">Enter a new or existing case identifier.</p>
        )}
      </form>

      {cases.length > 0 ? (
        <section className="recent-cases" aria-labelledby="recent-cases-title">
          <h2 id="recent-cases-title">Recent cases</h2>
          <ul>
            {cases.map((thread) => (
              <li key={thread.case_id}>
                <Link href={`/cases/${encodeURIComponent(thread.case_id)}/threads/${encodeURIComponent(thread.thread_id)}`}>
                  <span>{thread.case_id}</span>
                  <small>Continue investigation</small>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
