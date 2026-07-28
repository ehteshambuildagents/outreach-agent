"use client";

import { useState } from "react";
import Link from "next/link";
import { Check, ArrowRight, ShieldCheck, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { SiteNav } from "@/components/marketing/site-nav";
import { SiteFooter } from "@/components/marketing/site-footer";
import { HeroForm } from "@/components/marketing/hero-form";
import { PRELAUNCH } from "@/lib/launch";
import { cn } from "@/lib/utils";

type Billing = "monthly" | "yearly";

// Cost-based. All-in cost per researched prospect is ~$0.35: website crawl
// (Firecrawl) + deep research (Tavily/Exa/Jina) ~$0.11, research/qualify/strategy
// on Haiku-4.5 ~$0.03, email + refine + 2 follow-ups on Sonnet ~$0.06, and the
// chat orchestration loop ~$0.14. So 50 => ~$17.50 cost @ $65 (73% margin),
// 100 => ~$35 @ $100 (65%). Enterprise is quoted per account. Yearly = 2 months
// free. Numbers are still being finalized against real telemetry.
const PLANS = [
  {
    name: "Starter",
    prospects: "50",
    tagline: "Validate outbound without hiring anyone.",
    monthly: 65,
    yearly: 54,
    cta: "Coming soon",
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
    yearly: 83,
    featured: true,
    cta: "Coming soon",
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
    // No monthly/yearly: Enterprise is quoted per account, so the card renders
    // "Custom" where the other two render a figure.
    monthly: null,
    yearly: null,
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

export default function PricingPage() {
  const [billing, setBilling] = useState<Billing>("monthly");

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

        {/* Billing toggle — updates every price together */}
        <div className="mt-8 inline-flex items-center gap-1 rounded-full border border-border bg-card p-1 text-sm shadow-[0_1px_2px_rgba(17,17,17,.04)]">
          {(["monthly", "yearly"] as const).map((b) => (
            <button
              key={b}
              onClick={() => setBilling(b)}
              className={cn(
                "relative rounded-full px-4 py-1.5 capitalize transition-colors",
                billing === b ? "bg-accent text-[color:var(--accent-ink)]" : "text-text-2 hover:text-text",
              )}
            >
              {b}
              {b === "yearly" && (
                <span
                  className={cn(
                    "ml-1.5 text-[10px] font-semibold",
                    billing === "yearly" ? "text-white/80" : "text-accent",
                  )}
                >
                  2 months free
                </span>
              )}
            </button>
          ))}
        </div>

        <div className="mt-12 grid items-start gap-5 lg:grid-cols-3">
          {PLANS.map((p) => {
            const price = billing === "monthly" ? p.monthly : p.yearly;
            const featured = "featured" in p && p.featured;
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
                        <span className="text-xs text-muted">/ mo{billing === "yearly" ? ", billed yearly" : ""}</span>
                      </>
                    )}
                  </div>
                  <div className="mt-1 text-xs text-accent">
                    <span className="font-mono">{p.prospects}</span> researched prospects / month
                  </div>

                  <Button asChild variant={featured ? "primary" : "secondary"} className="mt-5 w-full">
                    <Link href={price === null ? "/contact" : PRELAUNCH ? "#waitlist" : "/sign-up"}>
                      {p.cta} <ArrowRight className="size-4" />
                    </Link>
                  </Button>

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
          // Every plan CTA on this page scrolls here, so the waitlist form has to
          // live on the page rather than bouncing people to the homepage.
          <div id="waitlist" className="mx-auto mt-14 max-w-md scroll-mt-24 text-center">
            <h2 className="font-display text-2xl font-semibold tracking-tight">
              Not open yet.
            </h2>
            <p className="mx-auto mt-3 text-sm leading-6 text-muted">
              Join the waitlist and we will email you the moment Saqua opens, with founding
              pricing locked in. Nothing else.
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
