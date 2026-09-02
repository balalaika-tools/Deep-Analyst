import { expect, test } from "@playwright/test";

test.beforeEach(async ({ request }) => {
  const response = await request.post("http://127.0.0.1:8181/__reset");
  expect(response.ok()).toBeTruthy();
});

test("home starts a new global conversation", async ({ page }, testInfo) => {
  await page.goto("/");
  if (testInfo.project.name === "mobile") {
    await page.getByRole("button", { name: "Open conversations" }).click();
  }
  await expect(page.getByRole("link", { name: "New conversation" })).toBeVisible();
  await expect(page.getByLabel("Message the investigation agent")).toBeVisible();
  await expect(page.getByText(["Case", "ID"].join(" "))).toHaveCount(0);
});

test("streams a verified investigation and assigns a thread route", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === "mobile", "Desktop flow is covered once");
  await page.goto("/");
  await page.getByLabel("Message the investigation agent").fill("Find the connection");
  await page.getByRole("button", { name: "Send message" }).click();

  await expect(page.getByText("Verified connection found across the reviewed evidence.")).toBeVisible();
  await expect(page).toHaveURL(/\/threads\/[A-Za-z0-9-]+$/);
  await expect(page.getByLabel("Message the investigation agent")).toBeEnabled();
});

test("keeps two fresh conversations independent over the shared corpus", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === "mobile", "Desktop flow is covered once");
  const composer = page.getByLabel("Message the investigation agent");

  await page.goto("/");
  await composer.fill("Trace the first connection in the shared evidence");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.getByText("Verified connection found across the reviewed evidence.")).toBeVisible();
  await expect(composer).toBeEnabled();
  await expect(page).toHaveURL(/\/threads\/[A-Za-z0-9-]+$/);
  const firstThreadPath = new URL(page.url()).pathname;
  await expect(page.locator(`a[href="${firstThreadPath}"]`)).toBeVisible();

  await page.getByRole("link", { name: "New conversation" }).click();
  await expect(page).toHaveURL("/");
  await expect(page.getByText("Trace the first connection in the shared evidence")).toHaveCount(0);

  await composer.fill("Trace a second connection in the shared evidence");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.getByText("Verified connection found across the reviewed evidence.")).toBeVisible();
  await expect(composer).toBeEnabled();
  await expect(page).toHaveURL(/\/threads\/[A-Za-z0-9-]+$/);
  await expect(page.getByText("Trace the first connection in the shared evidence")).toHaveCount(0);
  const secondThreadPath = new URL(page.url()).pathname;
  expect(secondThreadPath).not.toBe(firstThreadPath);

  await page.locator(`a[href="${firstThreadPath}"]`).click();
  await expect(page).toHaveURL(new URL(firstThreadPath, page.url()).toString());
  await page.waitForLoadState("networkidle");
  await expect(page.getByText("Trace a second connection in the shared evidence")).toHaveCount(0);
  await expect(page.getByText("Trace the first connection in the shared evidence")).toBeVisible();
  await expect(page.getByText("Verified connection found across the reviewed evidence.")).toBeVisible();
});

test("opens paginated conversation history using thread identity", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === "mobile", "Desktop flow is covered once");
  await page.goto("/threads/thread-1");
  await expect(page.getByText("Trace account 77")).toBeVisible();
  await expect(page.getByText("1 evidence source")).toBeVisible();
  await page.getByRole("button", { name: "Load more" }).click();
  await page.getByRole("link", { name: /conversation thread-2/i }).click();
  await expect(page).toHaveURL("/threads/thread-2");
  await expect(page.getByText("Review the second corpus segment")).toBeVisible();
});

test("retries, cancels, and reconciles bounded failures", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === "mobile", "Desktop flow is covered once");
  await page.goto("/");
  const composer = page.getByLabel("Message the investigation agent");
  await composer.fill("Please retry this lookup");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.locator(".turn-notice")).toContainText("temporarily unavailable");
  await page.getByRole("button", { name: "Retry investigation" }).click();
  await expect(page.getByText("Verified connection found across the reviewed evidence.")).toBeVisible();

  await page.goto("/");
  const freshComposer = page.getByLabel("Message the investigation agent");
  await freshComposer.fill("Run a slow investigation");
  await page.getByRole("button", { name: "Send message" }).click();
  await page.getByRole("button", { name: "Cancel investigation" }).click();
  await expect(freshComposer).toBeEnabled();
});

test("deletes an active conversation and preserves failed deletion", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === "mobile", "Desktop flow is covered once");
  await page.goto("/threads/thread-1");
  await page.getByRole("button", { name: "Delete conversation thread-1" }).click();
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(page).toHaveURL("/");

  await page.getByRole("button", { name: "Delete conversation thread-fail" }).click();
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(page.locator(".dialog-error")).toContainText("durably confirmed");
});

test("mobile drawer returns focus", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "Mobile-only navigation check");
  await page.goto("/");
  const trigger = page.getByRole("button", { name: "Open conversations" });
  await trigger.click();
  await expect(page.getByRole("complementary", { name: "Conversation navigation" })).toBeVisible();
  await page.getByRole("button", { name: "Close conversations" }).last().click();
  await expect(trigger).toBeFocused();
});

test("legacy routes redirect and missing threads stay bounded", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === "mobile", "Desktop flow is covered once");
  await page.goto(`/${["cases", "legacy"].join("/")}`);
  await expect(page).toHaveURL("/");
  await page.goto("/threads/unknown-thread");
  await expect(page.getByRole("heading", { name: "This conversation isn’t available." })).toBeVisible();
});
