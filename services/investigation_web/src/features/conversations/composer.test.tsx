import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Composer } from "./composer";

describe("Composer", () => {
  it("submits trimmed text with Enter and keeps Shift+Enter for multiline input", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<Composer busy={false} canRetry={false} onCancel={vi.fn()} onRetry={vi.fn()} onSubmit={onSubmit} />);
    const input = screen.getByLabelText("Message the investigation agent");

    await user.type(input, "first{shift>}{enter}{/shift}second");
    expect(onSubmit).not.toHaveBeenCalled();
    await user.keyboard("{Enter}");
    expect(onSubmit).toHaveBeenCalledWith("first\nsecond");
    expect(input).toHaveValue("");
  });

  it("switches to cancellation while busy", async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    render(<Composer busy canRetry={false} onCancel={onCancel} onRetry={vi.fn()} onSubmit={vi.fn()} />);
    expect(screen.getByLabelText("Message the investigation agent")).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Cancel investigation" }));
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("offers an explicit retry action", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    render(<Composer busy={false} canRetry onCancel={vi.fn()} onRetry={onRetry} onSubmit={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Retry investigation" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});
