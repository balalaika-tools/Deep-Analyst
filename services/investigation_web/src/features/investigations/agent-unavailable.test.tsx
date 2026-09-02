import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AgentUnavailable } from "./agent-unavailable";

describe("AgentUnavailable", () => {
  it("keeps the frontend useful and points operators to the ingestion failure logs", () => {
    render(<AgentUnavailable retryHref="/threads/thread-1" />);

    expect(
      screen.getByRole("heading", { name: "Investigation backend unavailable" }),
    ).toBeVisible();
    expect(screen.getByText("Operational")).toBeVisible();
    expect(screen.getByText("Unavailable")).toBeVisible();
    expect(screen.getByRole("link", { name: "Try again" })).toHaveAttribute(
      "href",
      "/threads/thread-1",
    );
    expect(screen.getByRole("link", { name: "Open Grafana logs" })).toHaveAttribute(
      "href",
      "http://localhost:3001/explore",
    );
    expect(screen.getByText(/ingestion\.run_failed/)).toBeVisible();
  });
});
