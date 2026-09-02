import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CaseLauncher } from "./case-launcher";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const threads = [
  { thread_id: "thread-1", case_id: "case-1", turn_id: "turn-1", status: "completed" as const, created_at: "2026-09-02T12:00:00Z" },
  { thread_id: "thread-2", case_id: "case-1", turn_id: "turn-2", status: "completed" as const, created_at: "2026-09-01T12:00:00Z" },
];

describe("CaseLauncher", () => {
  beforeEach(() => push.mockReset());

  it("opens a valid case from the keyboard", async () => {
    const user = userEvent.setup();
    render(<CaseLauncher recentThreads={threads} />);
    await user.type(screen.getByLabelText("Case ID"), "  case:new-7  {Enter}");
    expect(push).toHaveBeenCalledWith("/cases/case%3Anew-7");
  });

  it("shows actionable validation and does not navigate", async () => {
    const user = userEvent.setup();
    render(<CaseLauncher recentThreads={[]} />);
    await user.type(screen.getByLabelText("Case ID"), "bad case");
    await user.click(screen.getByRole("button", { name: "Open workspace" }));
    expect(screen.getByRole("alert")).toHaveTextContent("letters, numbers");
    expect(push).not.toHaveBeenCalled();
  });

  it("shows each recent case once", () => {
    render(<CaseLauncher recentThreads={threads} />);
    expect(screen.getAllByRole("link", { name: /case-1/i })).toHaveLength(1);
  });
});
