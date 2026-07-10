import Link from "next/link";
import { Check, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

const plans = [
  {
    name: "Free",
    price: "$0",
    desc: "Validate Saqua with a tiny list.",
    cta: "Start free",
    features: ["25 researched prospects", "1 campaign", "Gmail connection", "Guard checks"],
  },
  {
    name: "Pro",
    price: "$49",
    desc: "For founders running weekly outbound.",
    cta: "Get Pro",
    featured: true,
    features: ["2,500 emails/month", "Unlimited campaigns", "AI research graph", "Reply detection", "Priority support"],
  },
  {
    name: "Business",
    price: "$149",
    desc: "For teams that need scale and controls.",
    cta: "Talk to sales",
    features: ["10,000 emails/month", "Team workspaces", "CRM integrations", "Advanced telemetry", "Dedicated onboarding"],
  },
];

export default function PricingPage() {
  return (
    <main className="min-h-screen bg-bg text-text">
      <header className="mx-auto flex h-16 max-w-7xl items-center justify-between px-6">
        <Link href="/" className="flex items-center gap-2 text-sm font-semibold">
          <span className="grid size-7 place-items-center rounded-md bg-accent text-white shadow-glow">
            <Sparkles className="size-4" />
          </span>
          Saqua
        </Link>
        <Button asChild variant="secondary" size="sm">
          <Link href="/sign-in">Open dashboard</Link>
        </Button>
      </header>
      <section className="mx-auto max-w-7xl px-6 py-20">
        <div className="mx-auto max-w-2xl text-center">
          <Badge tone="accent" dot>Pricing</Badge>
          <h1 className="mt-5 text-4xl font-semibold tracking-tight md:text-5xl">Simple plans for careful outbound.</h1>
          <p className="mt-4 text-sm leading-6 text-text-2">
            Start small, keep Guard strict, and scale only when your emails are worth sending.
          </p>
        </div>
        <div className="mt-12 grid gap-4 lg:grid-cols-3">
          {plans.map((plan) => (
            <Card
              key={plan.name}
              className={`relative p-6 ${plan.featured ? "border-accent-line bg-[linear-gradient(180deg,rgba(124,92,255,.13),rgba(19,19,24,1))] shadow-glow" : ""}`}
            >
              {plan.featured && <Badge tone="accent" className="absolute right-5 top-5">Popular</Badge>}
              <div className="text-lg font-semibold">{plan.name}</div>
              <p className="mt-2 min-h-10 text-sm text-muted">{plan.desc}</p>
              <div className="mt-8 flex items-end gap-1">
                <span className="text-4xl font-semibold">{plan.price}</span>
                <span className="pb-1 text-sm text-muted">/ month</span>
              </div>
              <Button asChild variant={plan.featured ? "primary" : "secondary"} className="mt-6 w-full">
                <Link href="/sign-up">{plan.cta}</Link>
              </Button>
              <div className="mt-6 space-y-3 border-t border-border-faint pt-6">
                {plan.features.map((feature) => (
                  <div key={feature} className="flex items-center gap-2 text-sm text-text-2">
                    <Check className="size-4 text-success" /> {feature}
                  </div>
                ))}
              </div>
            </Card>
          ))}
        </div>
      </section>
    </main>
  );
}
