import { test, expect, type Page, type Route } from "@playwright/test";
import { api } from "../src/lib/api";
import { checkoutPlanId, resolveCta, resumeCheckoutPlan, signInUrl } from "../src/lib/pricing";
import { safeInternalPath, withRedirect } from "../src/lib/redirect";
import { appRouteDecision } from "../src/lib/access-gate";

/**
 * Pricing → checkout coverage. Two layers, both deterministic and free:
 *
 *   1. Pure logic (no browser): the name→backend-plan-id mapping and the CTA
 *      decision, plus the api.checkout contract with global.fetch stubbed — this is
 *      where "Starter creates a Pro checkout" / "Growth creates a Max checkout" /
 *      loading + error handling / "Enterprise does not create a checkout" are
 *      proven against the real request body, with no Clerk session required.
 *   2. Browser: the REAL /pricing page (logged out, which is Clerk's state in E2E)
 *      — a paid CTA sends the visitor through sign-in with a return path, Enterprise
 *      routes to contact, and no paid button touches the waitlist.
 */

// ── 1. Pure mapping + decision ───────────────────────────────────────────────
test.describe("pricing checkout mapping", () => {
  test("Starter maps to the canonical Pro plan id", () => {
    expect(checkoutPlanId("Starter")).toBe("pro");
  });

  test("Growth maps to the canonical Max plan id", () => {
    expect(checkoutPlanId("Growth")).toBe("max");
  });

  test("Enterprise / Free are not self-serve purchasable", () => {
    expect(checkoutPlanId("Enterprise")).toBeNull();
    expect(checkoutPlanId("Free")).toBeNull();
  });

  test("a signed-in member starts a real checkout for the mapped plan", () => {
    expect(resolveCta("Starter", { isLoaded: true, isSignedIn: true })).toEqual({
      kind: "checkout",
      planId: "pro",
    });
    expect(resolveCta("Growth", { isLoaded: true, isSignedIn: true })).toEqual({
      kind: "checkout",
      planId: "max",
    });
  });

  test("a logged-out visitor is sent through sign-in and returned to checkout", () => {
    const action = resolveCta("Starter", { isLoaded: true, isSignedIn: false });
    expect(action.kind).toBe("signin");
    if (action.kind !== "signin") throw new Error("expected signin");
    expect(action.url).toBe(signInUrl("pro"));
    // The return path resumes checkout on /pricing, never the waitlist.
    expect(decodeURIComponent(action.url)).toContain("/pricing?checkout=pro");
    expect(action.url).not.toContain("waitlist");
  });

  test("the click is ignored while Clerk is still hydrating", () => {
    expect(resolveCta("Starter", { isLoaded: false, isSignedIn: false })).toEqual({ kind: "wait" });
  });

  test("Enterprise resolves to the contact flow, never a checkout", () => {
    expect(resolveCta("Enterprise", { isLoaded: true, isSignedIn: true })).toEqual({ kind: "contact" });
  });
});

// ── resume-after-auth mapping ────────────────────────────────────────────────
test.describe("resume checkout after auth", () => {
  test("a pro return flag resumes the Pro checkout", () => {
    expect(resumeCheckoutPlan("?checkout=pro")).toBe("pro");
  });

  test("a max return flag resumes the Max checkout", () => {
    expect(resumeCheckoutPlan("?checkout=max&foo=1")).toBe("max");
  });

  test("an unknown or tampered flag resumes nothing", () => {
    expect(resumeCheckoutPlan("?checkout=platinum")).toBeNull();
    expect(resumeCheckoutPlan("?checkout=free")).toBeNull();
    expect(resumeCheckoutPlan("")).toBeNull();
  });
});

// ── page-reachability gate: paid users are never blocked by Clerk metadata ───
test.describe("app-access middleware decision", () => {
  test("any signed-in account reaches the app, regardless of Clerk appAccess", () => {
    // The decision no longer takes an appAccess flag at all: a just-paid buyer whose
    // Clerk publicMetadata carries no review flag is allowed through exactly like any
    // other signed-in account. null == allow (page renders).
    expect(appRouteDecision({ isSignedIn: true })).toBeNull();
  });

  test("a logged-out visitor is sent to the marketing home", () => {
    expect(appRouteDecision({ isSignedIn: false })).toBe("/");
  });
});

// ── internal-redirect safety (sign-in / sign-up return paths) ────────────────
test.describe("redirect safety", () => {
  test("same-origin single-slash paths are honored", () => {
    expect(safeInternalPath("/pricing?checkout=pro")).toBe("/pricing?checkout=pro");
    expect(safeInternalPath("/dashboard")).toBe("/dashboard");
  });

  test("external and protocol-based redirects are rejected", () => {
    expect(safeInternalPath("https://evil.com")).toBeNull();
    expect(safeInternalPath("http://evil.com")).toBeNull();
    expect(safeInternalPath("//evil.com")).toBeNull(); // protocol-relative
    expect(safeInternalPath("/\\evil.com")).toBeNull(); // backslash host trick
    expect(safeInternalPath("javascript:alert(1)")).toBeNull();
    expect(safeInternalPath("mailto:a@b.com")).toBeNull();
    expect(safeInternalPath("evil.com")).toBeNull(); // bare host, no leading slash
    expect(safeInternalPath(undefined)).toBeNull();
  });

  test("withRedirect carries only a validated path, and drops an unsafe one", () => {
    expect(withRedirect("/sign-up", "/pricing?checkout=max")).toBe(
      "/sign-up?redirect_url=" + encodeURIComponent("/pricing?checkout=max"),
    );
    expect(withRedirect("/sign-up", "https://evil.com")).toBe("/sign-up");
    expect(withRedirect("/sign-up", null)).toBe("/sign-up");
  });
});

// ── api.checkout request contract ────────────────────────────────────────────
test.describe("api.checkout contract", () => {
  test("Starter posts { plan: 'pro' } and follows the returned checkout URL", async () => {
    const calls: { url: string; body: unknown }[] = [];
    const orig = global.fetch;
    global.fetch = (async (url: unknown, init: RequestInit | undefined) => {
      calls.push({ url: String(url), body: init?.body ? JSON.parse(init.body as string) : null });
      return new Response(JSON.stringify({ url: "https://checkout.lemonsqueezy.test/pro" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }) as typeof fetch;
    try {
      const r = await api.checkout(checkoutPlanId("Starter")!);
      expect(r.ok).toBe(true);
      if (r.ok) expect(r.data.url).toBe("https://checkout.lemonsqueezy.test/pro");
      expect(calls).toHaveLength(1);
      expect(calls[0].url).toContain("/api/billing/checkout");
      expect(calls[0].body).toEqual({ plan: "pro", interval: "monthly" });
    } finally {
      global.fetch = orig;
    }
  });

  test("Growth posts { plan: 'max' }", async () => {
    const calls: { body: unknown }[] = [];
    const orig = global.fetch;
    global.fetch = (async (_url: unknown, init: RequestInit | undefined) => {
      calls.push({ body: init?.body ? JSON.parse(init.body as string) : null });
      return new Response(JSON.stringify({ url: "https://checkout.lemonsqueezy.test/max" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }) as typeof fetch;
    try {
      await api.checkout(checkoutPlanId("Growth")!);
      expect(calls[0].body).toEqual({ plan: "max", interval: "monthly" });
    } finally {
      global.fetch = orig;
    }
  });

  test("a failed checkout returns a typed error the UI can surface", async () => {
    const orig = global.fetch;
    global.fetch = (async () =>
      new Response(JSON.stringify({ detail: "Billing isn't configured." }), {
        status: 503,
        headers: { "content-type": "application/json" },
      })) as typeof fetch;
    try {
      const r = await api.checkout("pro");
      expect(r.ok).toBe(false);
      if (!r.ok) {
        expect(r.status).toBe(503);
        expect(r.error).toContain("Billing isn't configured.");
      }
    } finally {
      global.fetch = orig;
    }
  });
});

// ── 2. Browser: the real /pricing page (logged out) ──────────────────────────
async function gotoPricing(page: Page) {
  // Pricing is a public route; no auth cookie needed. Catch-all API mock so the
  // page never hangs on a real backend (it makes no /api call on mount anyway).
  await page.route("**/api/**", (route: Route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "{}" }),
  );
  await page.goto("/pricing");
  // Wait until Clerk has hydrated so the paid CTA is enabled (not the disabled
  // !isLoaded state); the button label is stable once ready.
  await expect(page.getByRole("button", { name: /Start with Starter/i })).toBeEnabled();
}

test.describe("pricing page (logged out)", () => {
  test("Starter sends a logged-out visitor to sign-in, returning to resume checkout", async ({ page }) => {
    await gotoPricing(page);
    await page.getByRole("button", { name: /Start with Starter/i }).click();
    await page.waitForURL(/\/sign-in/);
    const url = new URL(page.url());
    expect(url.pathname).toBe("/sign-in");
    expect(url.searchParams.get("redirect_url")).toBe("/pricing?checkout=pro");
    expect(page.url()).not.toContain("waitlist");
  });

  test("Growth sends a logged-out visitor to sign-in with the max return path", async ({ page }) => {
    await gotoPricing(page);
    await page.getByRole("button", { name: /Start with Growth/i }).click();
    await page.waitForURL(/\/sign-in/);
    expect(new URL(page.url()).searchParams.get("redirect_url")).toBe("/pricing?checkout=max");
  });

  test("Enterprise routes to the contact flow, not checkout", async ({ page }) => {
    await gotoPricing(page);
    await page.getByRole("link", { name: /Talk to us/i }).click();
    await page.waitForURL(/\/contact/);
    expect(new URL(page.url()).pathname).toBe("/contact");
  });

  test("no paid CTA says 'Coming soon' or points at the waitlist", async ({ page }) => {
    await gotoPricing(page);
    await expect(page.getByRole("button", { name: /Coming soon/i })).toHaveCount(0);
    // The two paid CTAs are real buttons (not waitlist anchors).
    await expect(page.getByRole("button", { name: /Start with Starter/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /Start with Growth/i })).toBeVisible();
  });

  test("the yearly toggle and '2 months free' are gone", async ({ page }) => {
    await gotoPricing(page);
    await expect(page.getByText(/2 months free/i)).toHaveCount(0);
    await expect(page.getByRole("button", { name: /^yearly$/i })).toHaveCount(0);
    // Prices are monthly only.
    await expect(page.getByText("$65").first()).toBeVisible();
    await expect(page.getByText("$100").first()).toBeVisible();
  });
});

// ── post-payment success page is public and hands off to the dashboard ───────
test.describe("checkout success page (logged out)", () => {
  test("is publicly reachable and confirms payment with a Continue to dashboard button", async ({
    page,
  }) => {
    // Logged out is Clerk's E2E state. If the middleware gated this route, a
    // logged-out visit would be redirected to "/" — so simply landing here proves
    // it is public.
    await page.goto("/checkout/success");
    await expect(page).toHaveURL(/\/checkout\/success/);
    await expect(page.getByRole("heading", { name: /Payment received/i })).toBeVisible();
    const cont = page.getByRole("link", { name: /Continue to dashboard/i });
    await expect(cont).toBeVisible();
    await expect(cont).toHaveAttribute("href", "/dashboard");
  });
});

// ── 3. New-customer auth funnel (sign-in ⇄ sign-up, return path) ─────────────
test.describe("new-customer auth funnel", () => {
  test("a brand-new visitor can reach sign-up from Starter, keeping the pro return path", async ({ page }) => {
    await gotoPricing(page);
    await page.getByRole("button", { name: /Start with Starter/i }).click();
    await page.waitForURL(/\/sign-in/);
    expect(new URL(page.url()).searchParams.get("redirect_url")).toBe("/pricing?checkout=pro");
    // From sign-in, a new customer picks "Create account" — and the return path rides along.
    await page.getByRole("link", { name: /Create account/i }).click();
    await page.waitForURL(/\/sign-up/);
    const url = new URL(page.url());
    expect(url.pathname).toBe("/sign-up");
    expect(url.searchParams.get("redirect_url")).toBe("/pricing?checkout=pro");
  });

  test("the sign-up page renders for a buyer and its 'Sign in' link keeps the checkout return path", async ({ page }) => {
    await page.goto("/sign-up?redirect_url=" + encodeURIComponent("/pricing?checkout=max"));
    // A valid checkout intent keeps us on /sign-up (NOT bounced to the waitlist).
    await expect(page).toHaveURL(/\/sign-up/);
    const signIn = page.getByRole("link", { name: /^Sign in$/i });
    await expect(signIn).toHaveAttribute(
      "href",
      "/sign-in?redirect_url=" + encodeURIComponent("/pricing?checkout=max"),
    );
  });

  test("a pre-launch visitor with no checkout intent is still sent to the waitlist", async ({ page }) => {
    await page.goto("/sign-up");
    // No valid return path + PRELAUNCH ⇒ the sign-up page redirects away to the
    // waitlist (the marketing home), never rendering an account form.
    await page.waitForURL((u) => new URL(u).pathname === "/");
    expect(new URL(page.url()).pathname).not.toBe("/sign-up");
  });

  test("an external redirect cannot force sign-up open, and is dropped on sign-in", async ({ page }) => {
    // External redirect on sign-up ⇒ no valid intent ⇒ waitlist (route not reopened).
    await page.goto("/sign-up?redirect_url=https://evil.com");
    await page.waitForURL((u) => new URL(u).pathname === "/");
    expect(new URL(page.url()).pathname).not.toBe("/sign-up");

    // External redirect on sign-in ⇒ the "Create account" link falls back to a bare
    // /sign-up (the unsafe path is dropped, never propagated).
    await page.goto("/sign-in?redirect_url=https://evil.com");
    await expect(page.getByRole("link", { name: /Create account/i })).toHaveAttribute("href", "/sign-up");
  });
});
