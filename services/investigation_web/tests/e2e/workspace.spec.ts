import { expect, test } from "@playwright/test";

test.beforeEach(async ({ request }) => {
  const response = await request.post("http://127.0.0.1:8181/__reset");
  expect(response.ok()).toBeTruthy();
});

test("opens a case from the home page", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Case ID").fill("case-1");
  await page.getByRole("button", { name: "Open workspace" }).click();
  await expect(page).toHaveURL("/cases/case-1");
  await expect(page.getByLabel("Message the investigation agent")).toBeVisible();
});

test("streams a new verified investigation progressively", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === "mobile", "Desktop flow is covered once");
  await page.goto("/cases/case-1");
  await page.getByLabel("Message the investigation agent").fill("Find the connection");
  await page.getByRole("button", { name: "Send message" }).click();

  await expect(page.getByRole("status", { name: "Investigation progress" })).toBeVisible();
  await expect(page.getByText("Searching evidence")).toBeVisible();
  await expect(page.getByLabel("Streaming assistant message")).toContainText("Verified connection");
  await expect(page.getByText("Verified connection found across the reviewed evidence.")).toBeVisible();
  await expect(page).toHaveURL(/\/cases\/case-1\/threads\/[A-Za-z0-9-]+$/);
  await expect(page.getByLabel("Message the investigation agent")).toBeEnabled();
  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    content: document.documentElement.scrollWidth,
  }));
  expect(dimensions.content).toBeLessThanOrEqual(dimensions.viewport);
});

test("opens history and navigates to a thread using its returned case", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === "mobile", "Desktop flow is covered once");
  await page.goto("/cases/case-1/threads/thread-1");
  await expect(page.getByText("Trace account 77")).toBeVisible();
  await expect(page.getByText("1 source")).toBeVisible();
  await page.getByRole("button", { name: "Load more" }).click();
  await page.getByRole("link", { name: /case-2/i }).click();
  await expect(page).toHaveURL("/cases/case-2/threads/thread-2");
  await expect(page.getByText("Review the second case")).toBeVisible();
});

test("retries a retryable failure as a new request", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === "mobile", "Desktop flow is covered once");
  await page.goto("/cases/case-1");
  await page.getByLabel("Message the investigation agent").fill("Please retry this lookup");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.locator(".turn-notice")).toContainText("temporarily unavailable");
  await page.getByRole("button", { name: "Retry investigation" }).click();
  await expect(page.getByText("Verified connection found across the reviewed evidence.")).toBeVisible();
});

test("cancels a slow request and restores the composer", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === "mobile", "Desktop flow is covered once");
  await page.goto("/cases/case-1");
  await page.getByLabel("Message the investigation agent").fill("Run a slow investigation");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.getByRole("button", { name: "Cancel investigation" })).toBeVisible();
  await page.getByRole("button", { name: "Cancel investigation" }).click();
  await expect(page.getByLabel("Message the investigation agent")).toBeEnabled();
});

test("reconciles a malformed stream without inventing an answer", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === "mobile", "Desktop flow is covered once");
  await page.goto("/cases/case-1");
  await page.getByLabel("Message the investigation agent").fill("Return a malformed stream");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.locator(".turn-notice")).toContainText("connection ended unexpectedly");
  await expect(page.getByLabel("Streaming assistant message")).toHaveCount(0);
});

test("confirms deletion and preserves a thread when deletion fails", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === "mobile", "Desktop flow is covered once");
  await page.goto("/cases/case-1/threads/thread-1");
  await page.getByRole("button", { name: "Delete conversation for case-1" }).first().click();
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(page).toHaveURL("/cases/case-1");

  await page.getByRole("button", { name: "Delete conversation for case-1" }).click();
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(page.locator(".dialog-error")).toContainText("durably confirmed");
  await expect(page.getByRole("link", { name: /case-1/i })).toBeVisible();
});

test("mobile conversation drawer opens and returns focus", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "Mobile-only navigation check");
  await page.goto("/cases/case-1");
  const trigger = page.getByRole("button", { name: "Open conversations" });
  await trigger.click();
  await expect(page.getByRole("complementary", { name: "Conversation navigation" })).toBeVisible();
  await page.getByRole("button", { name: "Close conversations" }).last().click();
  await expect(trigger).toBeFocused();
  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    content: document.documentElement.scrollWidth,
  }));
  expect(dimensions.content).toBeLessThanOrEqual(dimensions.viewport);
});

test("invalid and missing conversation routes render bounded not-found states", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === "mobile", "Desktop flow is covered once");
  await page.goto("/cases/bad%20case");
  await expect(page.getByRole("heading", { name: "This conversation isn’t available." })).toBeVisible();
  await page.goto("/cases/case-1/threads/unknown-thread");
  await expect(page.getByRole("heading", { name: "This conversation isn’t available." })).toBeVisible();
});
