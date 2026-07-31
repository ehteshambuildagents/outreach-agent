/**
 * Deterministic responsive-layout E2E for the demo chat shell. Mocked /api, so no
 * backend and no spend. Asserts the composer stays visible and the page never
 * scrolls horizontally across desktop/laptop/tablet/mobile widths and at 80/125/
 * 150% zoom (emulated via CSS `zoom` on the root — closer to real browser zoom
 * than a viewport-width substitution), plus the exhausted-demo waitlist CTA.
 */
import { test, expect, type Page, type Route } from "@playwright/test";

const FUTURE = Math.floor(Date.now() / 1000) + 3600;

async function mocks(page: Page) {
  await page.route("**/api/**", async (route: Route) => {
    const p = new URL(route.request().url()).pathname;
    const json = (b: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(b) });
    if (p === "/api/demo/session") return json({ active: true, expires_at: FUTURE, turns_used: 1, turns_limit: 5 });
    if (p === "/api/conversations") return json({ conversations: [] });
    return json({});
  });
}
async function goto(page: Page) {
  await page.context().addCookies([
    { name: "saqua_demo_exp", value: String(FUTURE), url: "http://localhost:3200" },
    { name: "saqua_demo", value: `demo_test.${FUTURE}.sig`, url: "http://localhost:3200" },
  ]);
  await page.addInitScript(() => window.localStorage.setItem("saqua_onboarded_v1", "1"));
  await page.goto("/ai");
}
async function overflow(page: Page) {
  return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
}

const WIDTHS = [
  { name: "desktop", w: 1440, h: 900 },
  { name: "laptop", w: 1280, h: 800 },
  { name: "tablet", w: 834, h: 1112 },
  { name: "mobile-390", w: 390, h: 844 },
];

for (const { name, w, h } of WIDTHS) {
  test(`responsive ${name} (${w}px): composer visible, no horizontal overflow`, async ({ page }) => {
    await mocks(page);
    await page.setViewportSize({ width: w, height: h });
    await goto(page);
    await expect(page.getByPlaceholder(/Enter to send/i)).toBeVisible();
    expect(await overflow(page)).toBeLessThanOrEqual(1);
  });
}

for (const zoom of [0.8, 1.25, 1.5]) {
  test(`zoom ${Math.round(zoom * 100)}%: composer visible, no horizontal overflow`, async ({ page }) => {
    await mocks(page);
    await page.setViewportSize({ width: 1280, height: 800 });
    await goto(page);
    // Emulate browser zoom by CSS zoom on the root, then re-measure overflow.
    await page.evaluate((z) => { (document.documentElement.style as any).zoom = String(z); }, zoom);
    await page.waitForTimeout(200);
    await expect(page.getByPlaceholder(/Enter to send/i)).toBeVisible();
    expect(await overflow(page)).toBeLessThanOrEqual(2);
  });
}

test("zero-messages exhausted demo keeps the waitlist CTA visible", async ({ page }) => {
  await page.route("**/api/**", async (route: Route) => {
    const p = new URL(route.request().url()).pathname;
    const json = (b: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(b) });
    if (p === "/api/demo/session") return json({ active: true, expires_at: FUTURE, turns_used: 5, turns_limit: 5 });
    if (p === "/api/conversations") return json({ conversations: [] });
    return json({});
  });
  await goto(page);
  await expect(page.getByRole("link", { name: /Join the waitlist/i }).first()).toBeVisible();
});
