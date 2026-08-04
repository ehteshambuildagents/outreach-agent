/**
 * Deterministic visual verification for the dashboard CTA / de-duplication fix and
 * the shared #F8F8FB app-shell background. Every /api call is mocked, so no backend
 * and no spend. A live demo cookie grants page reachability (see middleware), which
 * is all these read-only pages need to render. Screenshots are written to SHOT_DIR
 * for eyeballing; the assertions encode the prompt's acceptance criteria.
 */
import { test, expect, type Page, type Route } from "@playwright/test";
import path from "node:path";

const FUTURE = Math.floor(Date.now() / 1000) + 3600;
const SHOT_DIR = process.env.SHOT_DIR || path.join(process.cwd(), "e2e-shots");

const ZERO_METRICS = { metrics: { emails_sent: 0, replies: 0, reply_rate: 0 }, by_state: {} };
const LIVE_METRICS = {
  metrics: { emails_sent: 128, replies: 9, reply_rate: 0.07 },
  by_state: { RUNNING: 1 },
};
const ONE_WORKFLOW = [
  {
    id: "wf_1",
    state: "RUNNING",
    company: "Acme Robotics",
    provider: "gmail",
    to: "founder@acme.example",
    current_step: 2,
    total_steps: 5,
    next_run_at: FUTURE,
    reply_detected: false,
    retry_count: 0,
    last_error: null,
    last_execution: FUTURE - 600,
  },
];

async function mocks(page: Page, opts: { workflows: unknown[]; metrics: unknown }) {
  await page.route("**/api/**", async (route: Route) => {
    const p = new URL(route.request().url()).pathname;
    const json = (b: unknown) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(b) });
    if (p === "/api/demo/session") return json({ active: true, expires_at: FUTURE, turns_used: 1, turns_limit: 5 });
    if (p === "/api/automation/workflows") return json({ workflows: opts.workflows });
    if (p === "/api/automation/metrics") return json(opts.metrics);
    if (p === "/api/conversations") return json({ conversations: [] });
    if (p === "/api/campaigns") return json({ campaigns: [] });
    if (p === "/api/prospects") return json({ prospects: [] });
    if (p === "/api/company") return json({ company: {} });
    if (p === "/api/oauth/accounts") return json({ accounts: [] });
    if (p === "/api/billing") return json({ plan: "free3", usage: {}, limits: {} });
    return json({});
  });
}

async function auth(page: Page) {
  await page.context().addCookies([
    { name: "saqua_demo_exp", value: String(FUTURE), url: "http://localhost:3200" },
    { name: "saqua_demo", value: `demo_test.${FUTURE}.sig`, url: "http://localhost:3200" },
  ]);
  await page.addInitScript(() => window.localStorage.setItem("saqua_onboarded_v1", "1"));
}

async function overflow(page: Page) {
  return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
}

const WIDTHS = [
  { name: "desktop", w: 1440, h: 900 },
  { name: "laptop", w: 1280, h: 800 },
  { name: "mobile", w: 390, h: 844 },
];

test.describe("dashboard — empty state", () => {
  for (const { name, w, h } of WIDTHS) {
    test(`empty @ ${name}: 2 CTAs, no dark card, no overflow`, async ({ page }) => {
      await auth(page);
      await mocks(page, { workflows: [], metrics: ZERO_METRICS });
      await page.setViewportSize({ width: w, height: h });
      await page.goto("/dashboard");
      await page.getByRole("heading", { name: "Dashboard" }).waitFor();
      // Give the mocked fetches a beat to resolve into the empty states.
      await expect(page.getByText("No campaigns yet.")).toBeVisible();

      // Exactly two "Create campaign" affordances: top-right + one empty-state button.
      await expect(page.getByRole("link", { name: /Create campaign/i })).toHaveCount(2);
      // The dark bottom CTA is NOT shown when there are no campaigns.
      await expect(page.getByText("Ready to launch the next campaign?")).toHaveCount(0);
      // The Active-campaigns empty state carries no button (no awkward action spacer).
      await expect(page.getByText("No campaigns running yet")).toBeVisible();

      expect(await overflow(page)).toBeLessThanOrEqual(1);
      await page.screenshot({ path: path.join(SHOT_DIR, `empty-${name}.png`), fullPage: true });
    });
  }
});

test.describe("dashboard — with a campaign", () => {
  for (const { name, w, h } of WIDTHS) {
    test(`populated @ ${name}: dark CTA readable, 2 CTAs, no overflow`, async ({ page }) => {
      await auth(page);
      await mocks(page, { workflows: ONE_WORKFLOW, metrics: LIVE_METRICS });
      await page.setViewportSize({ width: w, height: h });
      await page.goto("/dashboard");
      await page.getByRole("heading", { name: "Dashboard" }).waitFor();

      const cta = page.getByText("Ready to launch the next campaign?");
      await expect(cta).toBeVisible();

      // Two affordances now: top-right + the CTA button (empty states are gone).
      await expect(page.getByRole("link", { name: /Create campaign/i })).toHaveCount(2);

      // Everything about the CTA is measured from the heading via closest(), so the
      // surface is unambiguous: a clean near-black (#13141A = rgb(19,20,26)), not
      // brown, with a subtle 1px border, 16px radius, pure-white heading. Geometry:
      // the button is right-aligned and vertically centered, and the card is compact.
      const m = await cta.evaluate((el) => {
        const surface = el.closest(".relative.mt-4") as HTMLElement;
        const btn = surface.querySelector("a[href='/campaigns/new']") as HTMLElement;
        const cs = getComputedStyle(surface);
        const hcs = getComputedStyle(el);
        const s = surface.getBoundingClientRect();
        const b = btn.getBoundingClientRect();
        const h = el.getBoundingClientRect();
        return {
          bg: cs.backgroundColor,
          radius: cs.borderTopLeftRadius,
          borderW: cs.borderTopWidth,
          headColor: hcs.color,
          surfaceH: s.height,
          rightGap: s.right - b.right,
          vMiss: Math.abs(b.top + b.height / 2 - (h.top + h.height / 2)),
        };
      });
      expect(m.bg).toBe("rgb(19, 20, 26)");
      expect(m.radius).toBe("16px");
      expect(m.borderW).toBe("1px");
      expect(m.headColor).toBe("rgb(255, 255, 255)");
      if (w >= 640) {
        expect(m.surfaceH).toBeLessThan(140); // compact single-row layout
        expect(m.rightGap).toBeLessThan(40); // right-aligned within the card padding
        expect(m.vMiss).toBeLessThan(30); // vertically centered on the heading line
      } else {
        expect(m.surfaceH).toBeLessThan(200); // mobile stacks button below text, still tight
      }

      expect(await overflow(page)).toBeLessThanOrEqual(1);
      await page.screenshot({ path: path.join(SHOT_DIR, `populated-${name}.png`), fullPage: true });
    });
  }
});

test.describe("shared app-shell background (#F8F8FB) is unharmed", () => {
  const PAGES = [
    { name: "dashboard", url: "/dashboard" },
    { name: "chat", url: "/ai" },
    { name: "campaigns", url: "/campaigns" },
    { name: "prospects", url: "/prospects" },
    { name: "settings", url: "/settings" },
  ];
  for (const { name, url } of PAGES) {
    test(`${name}: neutral bg + no horizontal overflow`, async ({ page }) => {
      await auth(page);
      await mocks(page, { workflows: [], metrics: ZERO_METRICS });
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto(url);
      await page.waitForTimeout(600);
      const bg = await page.evaluate(() => {
        const el = document.querySelector(".app-shell-viewport");
        return el ? getComputedStyle(el).backgroundColor : "missing";
      });
      expect(bg).toBe("rgb(248, 248, 251)");
      expect(await overflow(page)).toBeLessThanOrEqual(1);
      await page.screenshot({ path: path.join(SHOT_DIR, `shell-${name}.png`) });
    });
  }
});
