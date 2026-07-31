import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * Deterministic conversation-restoration lifecycle E2E. Every /api response is
 * mocked here, so the REAL Next app (middleware + client state) is exercised
 * without the backend — no API spend, fully reproducible. Uses a fixed demo
 * cookie so the middleware treats us as a live demo visitor (page reachability).
 */

const FUTURE = Math.floor(Date.now() / 1000) + 3600;

// ── Seed data ──────────────────────────────────────────────────────────────
function prospect(company: string, website: string, score: number, band: "strong" | "possible") {
  return {
    company, website, status: "discovered", score, fit_level: "company", band,
    priority: "none", recommendation: "", recommended: band === "strong",
    score_reason: `${company} matches`, preview: `${company}: matched your search.`,
    actions: ["research_prospect"],
    detail: { match_reasons: [`${company} is hiring an SDR`], hiring: { verified: true, match: "role", summary: "Live SDR posting" }, tier: "company", kind: "company" },
  };
}

const CONV1 = {
  id: "seed1",
  title: "Find SaaS companies hiring SDRs",
  active_company: null,
  messages: [
    { role: "user", kind: "text", content: "Find SaaS companies hiring SDRs", data: null },
    { role: "assistant", kind: "text", content: "I'll search for SaaS companies that are actively hiring SDRs.", data: null },
    {
      role: "assistant", kind: "prospects", content: "Found 2 qualified companies matching that.",
      data: {
        prospects: [prospect("Anthropic", "anthropic.com", 86, "possible"), prospect("OpenAI", "openai.com", 72, "possible")],
        summary: { total: 2, discovered: 2, considered: 61, demoted: 12, top: "Anthropic (86% match)" },
        quality: {},
      },
    },
  ],
};

const CONV2 = {
  id: "seed2",
  title: "Score my list: Stripe",
  active_company: null,
  messages: [
    { role: "user", kind: "text", content: "Score my list: Stripe", data: null },
    {
      role: "assistant", kind: "prospects", content: "Scored 1 company.",
      data: { prospects: [prospect("Stripe", "stripe.com", 64, "possible")], summary: { total: 1, discovered: 1, top: "Stripe (64% match)" }, quality: {} },
    },
  ],
};

const SUMMARIES = {
  conversations: [
    { id: "seed1", title: CONV1.title, updated_at: Date.now() / 1000 },
    { id: "seed2", title: CONV2.title, updated_at: Date.now() / 1000 - 60 },
  ],
};

// ── Route mocking ────────────────────────────────────────────────────────────
async function installApiMocks(page: Page, opts: { delayConv1Ms?: number; turnsUsed?: number } = {}) {
  await page.route("**/api/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

    if (p === "/api/demo/session") return json({ active: true, expires_at: FUTURE, turns_used: opts.turnsUsed ?? 1, turns_limit: 5 });
    if (p === "/api/conversations") return json(SUMMARIES);
    if (p === "/api/conversations/seed1") {
      if (opts.delayConv1Ms) await new Promise((r) => setTimeout(r, opts.delayConv1Ms));
      return json(CONV1);
    }
    if (p === "/api/conversations/seed2") return json(CONV2);
    return json({});
  });
}

async function gotoDemoApp(page: Page) {
  await page.context().addCookies([
    { name: "saqua_demo_exp", value: String(FUTURE), url: "http://localhost:3200" },
    { name: "saqua_demo", value: `demo_test.${FUTURE}.sig`, url: "http://localhost:3200" },
  ]);
  // Suppress the first-run onboarding tour, whose full-screen overlay would
  // otherwise intercept clicks. Runs before every load, including reload().
  await page.addInitScript(() => window.localStorage.setItem("saqua_onboarded_v1", "1"));
  await page.goto("/ai");
}

// The transcript lives in this section; the sidebar (RECENTS) is outside it, so
// scoping here proves a message rendered in the CONVERSATION, not the sidebar.
const transcript = (page: Page) => page.locator('[data-tour="artifact-hint"]');
const recentsRow = (page: Page, title: string) => page.getByRole("button", { name: title, exact: true });

test.beforeEach(async ({ page }) => {
  await installApiMocks(page);
});

test("selecting a conversation from RECENTS renders all three messages once, with the prospect card and scores", async ({ page }) => {
  await gotoDemoApp(page);
  // Starts on the empty state (no conversation selected yet).
  await expect(page.getByText("Who should we go after?")).toBeVisible();

  await recentsRow(page, CONV1.title).click();

  const t = transcript(page);
  await expect(t.getByText("Find SaaS companies hiring SDRs")).toHaveCount(1);
  await expect(t.getByText("I'll search for SaaS companies that are actively hiring SDRs.")).toHaveCount(1);
  // The prospects card restored with its companies + scores.
  await expect(t.getByText("Anthropic", { exact: true })).toHaveCount(1);
  await expect(t.getByText("OpenAI", { exact: true })).toHaveCount(1);
  await expect(t.getByText(/86% match/)).toBeVisible();
  await expect(t.getByText(/12 aggregators filtered out/)).toBeVisible();
  await expect(page.getByText("Who should we go after?")).toHaveCount(0);
});

test("switching to another thread and back restores the prospect card again", async ({ page }) => {
  await gotoDemoApp(page);
  await recentsRow(page, CONV1.title).click();
  await expect(transcript(page).getByText(/86% match/)).toBeVisible();

  await recentsRow(page, CONV2.title).click();
  await expect(transcript(page).getByText("Stripe", { exact: true })).toBeVisible();
  await expect(transcript(page).getByText("Anthropic", { exact: true })).toHaveCount(0);

  await recentsRow(page, CONV1.title).click();
  await expect(transcript(page).getByText("Anthropic", { exact: true })).toHaveCount(1);
  await expect(transcript(page).getByText(/86% match/)).toBeVisible();
});

test("a slow first-conversation response cannot overwrite a later selection", async ({ page }) => {
  await installApiMocks(page, { delayConv1Ms: 1500 });
  await gotoDemoApp(page);
  await recentsRow(page, CONV1.title).click(); // slow (1.5s)
  await recentsRow(page, CONV2.title).click(); // fast

  await expect(transcript(page).getByText("Stripe", { exact: true })).toBeVisible();
  // Wait past conv1's delayed resolution and prove it did NOT clobber conv2.
  await page.waitForTimeout(2000);
  await expect(transcript(page).getByText("Stripe", { exact: true })).toBeVisible();
  await expect(transcript(page).getByText("Anthropic", { exact: true })).toHaveCount(0);
});

test("refresh on a valid demo session stays on /ai and does not redirect to /", async ({ page }) => {
  await gotoDemoApp(page);
  await recentsRow(page, CONV1.title).click();
  await expect(transcript(page).getByText(/86% match/)).toBeVisible();

  await page.reload();
  await expect(page).toHaveURL(/\/ai$/); // middleware must not bounce a live demo to /
});

test("after refresh, re-selecting the same conversation renders it once (no duplication/omission)", async ({ page }) => {
  await gotoDemoApp(page);
  await recentsRow(page, CONV1.title).click();
  await expect(transcript(page).getByText(/86% match/)).toBeVisible();

  await page.reload();
  await recentsRow(page, CONV1.title).click();

  const t = transcript(page);
  await expect(t.getByText("Find SaaS companies hiring SDRs")).toHaveCount(1);
  await expect(t.getByText("Anthropic", { exact: true })).toHaveCount(1);
  await expect(t.getByText("OpenAI", { exact: true })).toHaveCount(1);
  await expect(t.getByText(/86% match/)).toBeVisible();
});

test("an exhausted demo session (5 of 5 used) replaces the composer with the waitlist prompt", async ({ page }) => {
  await installApiMocks(page, { turnsUsed: 5 });
  await gotoDemoApp(page);
  await expect(page.getByText(/every message in this demo/i)).toBeVisible();
  await expect(page.getByRole("link", { name: /Join the waitlist/i }).first()).toBeVisible();
  await expect(page.getByPlaceholder(/Enter to send/i)).toHaveCount(0); // no input to spend more
});

test("the composer is visible and the page does not scroll horizontally at mobile width", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await gotoDemoApp(page);
  await expect(page.getByPlaceholder(/Enter to send/i)).toBeVisible();
  // No horizontal overflow of the body at a phone width.
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});
