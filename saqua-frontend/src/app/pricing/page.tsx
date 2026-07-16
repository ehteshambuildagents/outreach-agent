"use client";

import { useState } from "react";
import Link from "next/link";
import { Check, ArrowRight, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Logo } from "@/components/ui/logo";
import { cn } from "@/lib/utils";

type Billing = "monthly" | "yearly";

// Cost-based at >=40% gross margin. All-in cost per researched prospect is ~$0.35:
// website crawl (Firecrawl) + deep research (Tavily/Exa/Jina) ~$0.11, research/
// qualify/strategy on Haiku-4.5 ~$0.03, email + refine + 2 follow-ups on Sonnet
// ~$0.06, and the chat orchestration loop ~$0.14. So 50 => ~$17.50 cost @ $39
// (55% margin), 100 => ~$35 @ $79 (56%), 300 => ~$105 @ $199 (47%). Yearly = 2
// months free. Numbers are still being finalized against real telemetry.
const PLANS = [
  {
    name: "Starter",
    prospects: "50",
    tagline: "Validate outbound without hiring anyone.",
    monthly: 39,
    yearly: 33,
    cta: "Start Starter",
    features: [
      "50 researched prospects / month",
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
    monthly: 79,
    yearly: 66,
    featured: true,
    cta: "Start Growth",
    features: [
      "100 researched prospects / month",
      "Everything in Starter",
      "Priority research queue",
      "Reply & campaign analytics",
      "Evidence-anchored follow-ups",
      "Email support",
    ],
  },
  {
    name: "Scale",
    prospects: "300+",
    tagline: "For teams scaling outbound properly.",
    monthly: 199,
    yearly: 166,
    cta: "Talk to us",
    features: [
      "300+ researched prospects / month",
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
    <main className="relative min-h-screen overflow-hidden bg-bg text-text">
      <div aria-hidden className="pointer-events-none absolute inset-0 -z-10">
        <div className="bloom-indigo absolute -left-40 -top-40 size-[620px] rounded-full" />
        <div className="bloom-teal absolute -right-48 top-[36%] size-[560px] rounded-full" />
      </div>

      <header className="mx-auto flex h-16 max-w-7xl items-center justify-between px-6">
        <Link href="/" className="flex items-center gap-2 text-sm font-semibold">
          <Logo className="h-6 w-auto" /> Saqua
        </Link>
        <div className="flex items-center gap-2">
          <Button asChild variant="ghost" size="sm">
            <Link href="/sign-in">Log in</Link>
          </Button>
          <Button asChild variant="primary" size="sm">
            <Link href="/sign-up">Get started</Link>
          </Button>
        </div>
      </header>

      <section className="mx-auto max-w-6xl px-6 pb-24 pt-12 text-center">
        <h1 className="text-4xl font-semibold tracking-tight md:text-5xl">
          Priced per prospect you actually research.
        </h1>
        <p className="mx-auto mt-4 max-w-xl text-sm leading-6 text-text-2">
          Every plan runs the full pipeline — research, scoring, writing, follow-ups,
          and reply detection. You&apos;re paying for prospects Saqua actually works, not seats.
        </p>

        {/* Billing toggle — updates every price together */}
        <div className="mt-8 inline-flex items-center gap-1 rounded-full border border-border bg-white/[0.03] p-1 text-sm">
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
                    billing === "yearly" ? "text-[color:var(--accent-ink)]/80" : "text-accent-hi",
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
                  "glass relative flex flex-col rounded-2xl border p-6 text-left transition-all duration-200",
                  featured
                    ? "border-accent-line shadow-glow lg:-mt-3 lg:pb-8"
                    : "border-border hover:-translate-y-0.5 hover:border-border-strong",
                )}
              >
                {featured && (
                  <div className="mb-3 inline-flex w-fit items-center gap-1.5 rounded-full border border-accent-line bg-accent-soft px-2.5 py-0.5 text-[11px] font-medium text-accent-hi">
                    <span className="size-1.5 animate-pulse-soft rounded-full bg-accent" /> Recommended
                  </div>
                )}
                <div className="text-sm font-semibold text-text">{p.name}</div>
                <p className="mt-1 text-xs leading-5 text-muted">{p.tagline}</p>

                <div className="mt-5 flex items-baseline gap-1">
                  <span className="font-mono text-4xl font-semibold tracking-tight text-text">${price}</span>
                  <span className="text-xs text-muted">/ mo{billing === "yearly" ? ", billed yearly" : ""}</span>
                </div>
                <div className="mt-1 text-xs text-accent-hi">
                  <span className="font-mono">{p.prospects}</span> researched prospects / month
                </div>

                <Button asChild variant={featured ? "primary" : "secondary"} className="mt-5 w-full">
                  <Link href="/sign-up">
                    {p.cta} <ArrowRight className="size-4" />
                  </Link>
                </Button>

                <ul className="mt-6 space-y-2.5">
                  {p.features.map((f) => (
                    <li key={f} className="flex items-start gap-2.5 text-sm text-text-2">
                      <Check className="mt-0.5 size-4 shrink-0 text-accent-hi" /> {f}
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>

        {/* Cost-basis honesty (required) */}
        <div className="mx-auto mt-10 flex max-w-2xl items-start gap-2.5 rounded-xl border border-warn-soft bg-warn-soft/30 px-4 py-3 text-left text-xs leading-5 text-text-2">
          <ShieldCheck className="mt-0.5 size-4 shrink-0 text-warn" />
          <span>
            <span className="font-medium text-text">Cost-based, still being finalized.</span> All-in
            compute is about $0.35 per researched prospect (~$17.50 / $35 / $105 a month for these
            tiers), and prices sit at a ≥40% gross margin on top. Final numbers may move once
            we&apos;ve tuned them against real usage.
          </span>
        </div>

        <p className="mt-8 text-xs text-muted">
          Starting out?{" "}
          <Link href="/sign-up" className="text-accent-hi hover:underline">
            The free plan researches 3 prospects
          </Link>{" "}
          so you can see a real draft before paying.
        </p>
      </section>
    </main>
  );
}
