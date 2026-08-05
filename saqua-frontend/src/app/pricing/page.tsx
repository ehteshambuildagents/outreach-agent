"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useUser } from "@clerk/nextjs";
import { ArrowRight, Check, Loader2, ShieldCheck, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { SiteNav } from "@/components/marketing/site-nav";
import { SiteFooter } from "@/components/marketing/site-footer";
import { HeroForm } from "@/components/marketing/hero-form";
import { PRELAUNCH } from "@/lib/launch";
import { api } from "@/lib/api";
import { checkoutPlanId, resolveCta, resumeCheckoutPlan, type CheckoutPlanId } from "@/lib/pricing";
import { cn } from "@/lib/utils";

// Cost-based. All-in cost per researched prospect is ~$0.35: website crawl
// (Firecrawl) + deep research (Tavily/Exa/Jina) ~$0.11, research/qualify/strategy
// on Haiku-4.5 ~$0.03, email + refine + 2 follow-ups on Sonnet ~$0.06, and the
// chat orchestration loop ~$0.14. So 50 => ~$17.50 cost @ $65 (73% margin),
// 100 => ~$35 @ $100 (65%). Enterprise is quoted per account. Only monthly Lemon
// Squeezy variants exist today, so the page prices per month and hides any yearly
// option until annual variants are configured.
const PLANS = [
  {
    name: "Starter",
    prospects: "50",
    tagline: "Validate outbound without hiring anyone.",
    monthly: 65,
    // Maps to the canonical "pro" plan the backend expects (Lemon Squeezy variant
    // 1984306). See lib/pricing.ts for the single source of that name→id mapping.
    cta: "Start with Starter",
    // The prospect allowance is already the headline under the price, so it is
    // deliberately NOT repeated as the first bullet: every card used to print it
    // twice, a few lines apart, which reads like a rendering bug.
    features: [
      "Unlimited campaigns",
      "Full 5-touch follow-up cadence",
      "Automatic reply detection",
      "Gmail or Outlook sending",
      "Deliverability & cost guard",
    ],
  },
  {
    name: "Growth",
    prospects: "100",
    tagline: "For founders running weekly outbound.",
    monthly: 100,
    featured: true,
    // Maps to the canonical "max" plan the backend expects (Lemon Squeezy variant
    // 1984314).
    cta: "Start with Growth",
    features: [
      "Everything in Starter",
      "Priority research queue",
      "Reply & campaign analytics",
      "Evidence-anchored follow-ups",
      "Email support",
    ],
  },
  {
    name: "Enterprise",
    prospects: "300+",
    tagline: "For teams scaling outbound properly.",
    // No monthly price: Enterprise is quoted per account, so the card renders
    // "Custom" where the other two render a figure, and its CTA stays on the
    // sales-assisted "Talk to us" (contact) flow — never a self-serve checkout.
    monthly: null,
    cta: "Talk to us",
    features: [
      "Everything in Growth",
      "Team seats & shared campaigns",
      "Higher rate limits",
      "Priority support",
      "Custom ICP tuning",
    ],
  },
] as const;

/**
 * The per-plan call to action. Enterprise is a plain contact link; the two paid
 * tiers run the REAL checkout: a logged-out visitor is sent through sign-in and
 * returned here to resume, and a signed-in member goes straight to the Lemon
 * Squeezy hosted checkout via the existing POST /api/billing/checkout endpoint.
 * No plan id or price is hardcoded against Lemon Squeezy here — only the canonical
 * backend plan id ("pro"/"max"), resolved in lib/pricing.ts.
 */
function PlanCta({ name, cta, featured }: { name: string; cta: string; featured: boolean }) {
  const { isLoaded, isSignedIn } = useUser();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const planId = checkoutPlanId(name); // null for Enterprise / non-purchasable

  const startCheckout = useCallback((id: CheckoutPlanId) => {
    setBusy(true); // also disables the button, so a double click can't open two checkouts
    setError("");
    void api.checkout(id).then((r) => {
      if (!r.ok) {
        setBusy(false);
        setError(r.error || "Could not start checkout. Please try again.");
        return;
      }
      // Hand off to Lemon Squeezy's hosted checkout — this leaves the app, so we
      // deliberately keep `busy` true (no reset) to hold the loading state.
      window.location.href = r.data.url;
    });
  }, []);

  // Resume checkout after the sign-in round-trip: /sign-in returns the visitor to
  // /pricing?checkout=<planId>, and the card whose id matches auto-starts once Clerk
  // reports them signed in. The flag is cleared first so a refresh can't loop.
  useEffect(() => {
    if (!planId || !isLoaded || !isSignedIn || busy) return;
    if (resumeCheckoutPlan(window.location.search) !== planId) return;
    // Clear the flag first so a refresh can't loop, then start the matching checkout.
    const params = new URLSearchParams(window.location.search);
    params.delete("checkout");
    const qs = params.toString();
    window.history.replaceState({}, "", `/pricing${qs ? `?${qs}` : ""}`);
    startCheckout(planId);
  }, [planId, isLoaded, isSignedIn, busy, startCheckout]);

  // Enterprise (and any non-purchasable plan): the sales-assisted contact flow.
  if (!planId) {
    return (
      <Button asChild variant="secondary" className="mt-5 w-full">
        <Link href="/contact">
          {cta} <ArrowRight className="size-4" />
        </Link>
      </Button>
    );
  }

  const onClick = () => {
    if (busy) return; // prevent duplicate clicks
    const action = resolveCta(name, { isLoaded, isSignedIn: isSignedIn === true });
    if (action.kind === "wait") return; // Clerk not hydrated yet; button is disabled anyway
    if (action.kind === "signin") {
      window.location.href = action.url; // sign in, then return to resume checkout
      return;
    }
    if (action.kind === "checkout") startCheckout(action.planId);
  };

  return (
    <div className="mt-5">
      <Button
        type="button"
        variant={featured ? "primary" : "secondary"}
        className="w-full"
        onClick={onClick}
        disabled={busy || !isLoaded}
        aria-busy={busy}
        data-plan={planId}
      >
        {busy ? (
          <>
            <Loader2 className="size-4 animate-spin" /> Opening checkout…
          </>
        ) : (
          <>
            {cta} <ArrowRight className="size-4" />
          </>
        )}
      </Button>
      {error && (
        <p role="alert" className="mt-2 text-xs leading-5 text-[color:var(--danger,#b42318)]">
          {error}
        </p>
      )}
    </div>
  );
}

export default function PricingPage() {
  return (
    // No bg here: it would paint over the -z-10 glow (see globals.css .page-light).
    <main className="relative min-h-screen overflow-clip text-text">
      <div aria-hidden className="hero-glow pointer-events-none absolute inset-x-0 top-0 -z-10 h-[600px]" />

      <SiteNav />

      <section className="mx-auto max-w-6xl px-6 pb-24 pt-36 text-center">
        <span className="inline-flex h-8 items-center gap-2 rounded-full border border-border bg-white px-4 text-xs font-medium shadow-[0_1px_2px_rgba(17,17,17,.04)]">
          <Sparkles className="size-3.5 text-accent" /> Transparent pricing, no surprises
        </span>
        <h1 className="mx-auto mt-7 max-w-[18ch] font-display text-4xl font-medium tracking-[-0.03em] md:text-6xl">
          Priced per prospect you <span className="grad-text">actually research</span>.
        </h1>
        <p className="mx-auto mt-5 max-w-xl text-base leading-7 text-muted">
          Every plan runs the full pipeline: research, scoring, writing, follow-ups, and reply
          detection. You&apos;re paying for prospects Saqua actually works, not seats.
        </p>

        <div className="mt-12 grid items-start gap-5 lg:grid-cols-3">
          {PLANS.map((p) => {
            const price = p.monthly;
            const featured = "featured" in p && p.featured === true;
            return (
              <div
                key={p.name}
                className={cn(
                  "relative rounded-2xl",
                  featured ? "bg-grad-brand p-[1.5px] shadow-pop lg:-mt-3" : "",
                )}
              >
                <div
                  className={cn(
                    "flex h-full flex-col rounded-2xl p-6 text-left",
                    featured
                      ? "bg-card lg:pb-8"
                      : "glass hover-lift border border-border shadow-card",
                  )}
                >
                  {featured && (
                    <div className="mb-3 inline-flex w-fit items-center gap-1.5 rounded-full border border-accent-line bg-accent-soft px-2.5 py-0.5 text-[11px] font-medium text-accent">
                      <span className="size-1.5 animate-pulse-soft rounded-full bg-accent" /> Recommended
                    </div>
                  )}
                  <div className="font-display text-lg font-semibold text-text">{p.name}</div>
                  <p className="mt-1 text-xs leading-5 text-muted">{p.tagline}</p>

                  <div className="mt-5 flex items-baseline gap-1">
                    {price === null ? (
                      <span className="font-display text-5xl font-semibold tracking-[-0.03em] text-text">Custom</span>
                    ) : (
                      <>
                        <span className="font-display text-5xl font-semibold tracking-[-0.03em] text-text">${price}</span>
                        <span className="text-xs text-muted">/ mo</span>
                      </>
                    )}
                  </div>
                  <div className="mt-1 text-xs text-accent">
                    <span className="font-mono">{p.prospects}</span> researched prospects / month
                  </div>

                  <PlanCta name={p.name} cta={p.cta} featured={featured} />

                  <ul className="mt-6 space-y-2.5">
                    {p.features.map((f) => (
                      <li key={f} className="flex items-start gap-2.5 text-sm text-text-2">
                        <Check className="mt-0.5 size-4 shrink-0 text-accent" /> {f}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            );
          })}
        </div>

        {/* Cost-basis honesty (required) */}
        <div className="mx-auto mt-10 flex max-w-2xl items-start gap-2.5 rounded-xl border border-warn-soft bg-warn-soft/40 px-4 py-3 text-left text-xs leading-5 text-text-2">
          <ShieldCheck className="mt-0.5 size-4 shrink-0 text-warn" />
          <span>
            <span className="font-medium text-text">Cost-based, still being finalized.</span> All-in
            compute is about $0.35 per researched prospect (~$17.50 / $35 / $105 a month for these
            tiers), and prices sit at a ≥40% gross margin on top. Final numbers may move once
            we&apos;ve tuned them against real usage.
          </span>
        </div>

        {PRELAUNCH ? (
          // The paid tiers now run real checkout; this block is only the free-plan /
          // "not open yet" path, where public signup is still closed.
          <div id="waitlist" className="mx-auto mt-14 max-w-md scroll-mt-24 text-center">
            <h2 className="font-display text-2xl font-semibold tracking-tight">
              New here?
            </h2>
            <p className="mx-auto mt-3 text-sm leading-6 text-muted">
              Public signup for the free plan is still closed. Join the waitlist and we will email
              you the moment it opens, with founding pricing locked in. Nothing else.
            </p>
            <HeroForm className="mt-6" source="pricing" />
          </div>
        ) : (
          <p className="mt-8 text-xs text-muted">
            Starting out?{" "}
            <Link href="/sign-up" className="text-accent hover:underline">
              The free plan researches 3 prospects
            </Link>{" "}
            so you can see a real draft before paying.
          </p>
        )}
      </section>

      <SiteFooter />
    </main>
  );
}
