import Link from "next/link";
import { ArrowRight, Bot, CheckCircle2, MailCheck, Radar, ShieldCheck, Sparkles, Workflow } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

const agentCards = [
  ["Research", "Finds founder signals and proof", Radar],
  ["Writer", "Drafts specific first emails", MailCheck],
  ["Guard", "Blocks weak or unsafe sends", ShieldCheck],
] as const;

const workflow = [
  "Discover qualified accounts",
  "Build evidence-backed research",
  "Write and guard every email",
  "Stop sequences when replies arrive",
];

export default function LandingPage() {
  return (
    <main className="min-h-screen overflow-hidden bg-bg text-text">
      <header className="mx-auto flex h-16 max-w-7xl items-center justify-between px-6">
        <Link href="/" className="flex items-center gap-2 text-sm font-semibold">
          <span className="grid size-7 place-items-center rounded-md bg-accent text-white shadow-glow">
            <Sparkles className="size-4" />
          </span>
          Saqua
        </Link>
        <nav className="hidden items-center gap-7 text-xs text-muted md:flex">
          <a href="#agents" className="hover:text-text">Product</a>
          <a href="#workflow" className="hover:text-text">How it works</a>
          <Link href="/pricing" className="hover:text-text">Pricing</Link>
          <Link href="/dashboard" className="hover:text-text">Dashboard</Link>
        </nav>
        <div className="flex items-center gap-2">
          <Button asChild variant="ghost" size="sm">
            <Link href="/sign-in">Log in</Link>
          </Button>
          <Button asChild variant="primary" size="sm">
            <Link href="/sign-up">Get started</Link>
          </Button>
        </div>
      </header>

      <section className="relative mx-auto grid min-h-[calc(100vh-64px)] max-w-7xl items-center gap-12 px-6 pb-20 pt-8 lg:grid-cols-[1fr_560px]">
        <div className="relative z-10 max-w-2xl">
          <div className="mb-8 inline-flex items-center gap-2 rounded-full border border-border bg-white/[0.03] px-3 py-1.5 text-xs text-text-2">
            <Bot className="size-3.5 text-accent-hi" /> AI SDR for founders
          </div>
          <h1 className="max-w-xl text-5xl font-semibold leading-[1.04] tracking-tight text-text md:text-6xl">
            The AI SDR that finds, writes & sends so you can focus on building.
          </h1>
          <p className="mt-6 max-w-xl text-sm leading-6 text-text-2">
            Saqua researches your ICP, finds the right founders, drafts personalized outreach, and automates follow-ups that actually get replies.
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-3">
            <Button asChild variant="primary" size="lg">
              <Link href="/sign-up">
                Get started for free <ArrowRight className="size-4" />
              </Link>
            </Button>
            <Button asChild variant="secondary" size="lg">
              <Link href="/sign-in">Book a demo</Link>
            </Button>
          </div>
          <div className="mt-10 flex flex-wrap items-center gap-4 text-xs text-muted">
            <div className="flex -space-x-2">
              {["EM", "MC", "ST", "DP", "AG"].map((name) => (
                <span key={name} className="grid size-7 place-items-center rounded-full border border-bg bg-raised text-[10px] text-text-2">
                  {name}
                </span>
              ))}
            </div>
            <span>Loved by builders</span>
            <span>No credit card required</span>
          </div>
        </div>

        <div className="relative min-h-[560px]">
          <div className="accent-glow absolute inset-0 scale-125 opacity-80" />
          <div className="absolute left-1/2 top-1/2 size-72 -translate-x-1/2 -translate-y-1/2 rounded-full border border-accent-line bg-[radial-gradient(circle_at_35%_30%,#bdafff_0%,#7c5cff_30%,#2a1b5c_70%,#08080b_100%)] shadow-[0_0_120px_rgba(124,92,255,.45)]" />
          <div className="absolute left-[12%] top-[28%] h-40 w-[80%] rotate-[-12deg] rounded-[50%] border border-accent-line opacity-70" />
          <FloatingCard className="left-0 top-24" title="Research" body="Finds founder + product signals" />
          <FloatingCard className="right-3 top-36" title="Strategy" body="Picks why-you / why-now" />
          <FloatingCard className="bottom-24 left-10" title="Automation" body="Follows up + stops on reply" />
          <FloatingCard className="bottom-14 right-10" title="Guard" body="Blocks generic drafts" />
        </div>
      </section>

      <section id="agents" className="mx-auto max-w-7xl px-6 py-20">
        <div className="mb-8 flex items-end justify-between gap-6">
          <div>
            <h2 className="text-2xl font-semibold tracking-tight">Agents that work like a careful SDR team</h2>
            <p className="mt-2 text-sm text-text-2">Each step is explicit, measured, and visible before anything sends.</p>
          </div>
          <Button asChild variant="secondary">
            <Link href="/sign-up">View flow</Link>
          </Button>
        </div>
        <div className="grid gap-4 md:grid-cols-3">
          {agentCards.map(([title, body, Icon]) => (
            <Card key={title} className="p-5">
              <div className="mb-6 grid size-10 place-items-center rounded-md bg-accent-soft text-accent-hi">
                <Icon className="size-5" />
              </div>
              <div className="font-medium">{title}</div>
              <p className="mt-2 text-sm text-muted">{body}</p>
            </Card>
          ))}
        </div>
      </section>

      <section id="workflow" className="mx-auto max-w-7xl px-6 pb-24">
        <Card className="overflow-hidden p-6 md:p-8">
          <div className="grid gap-8 lg:grid-cols-[360px_1fr]">
            <div>
              <div className="mb-4 grid size-10 place-items-center rounded-md bg-accent-soft text-accent-hi">
                <Workflow className="size-5" />
              </div>
              <h2 className="text-2xl font-semibold tracking-tight">A launch path with guardrails</h2>
              <p className="mt-3 text-sm leading-6 text-text-2">
                Prospect discovery, research, qualification, writing, and deliverability checks stay in one auditable flow.
              </p>
            </div>
            <div className="grid gap-3">
              {workflow.map((step, index) => (
                <div key={step} className="flex items-center gap-3 rounded-md border border-border bg-white/[0.02] p-4">
                  <span className="grid size-7 place-items-center rounded-md bg-accent text-xs font-semibold text-white">{index + 1}</span>
                  <span className="text-sm text-text-2">{step}</span>
                  <CheckCircle2 className="ml-auto size-4 text-success" />
                </div>
              ))}
            </div>
          </div>
        </Card>
      </section>
    </main>
  );
}

function FloatingCard({ className, title, body }: { className?: string; title: string; body: string }) {
  return (
    <div className={`absolute w-44 rounded-lg border border-border bg-card/90 p-3 shadow-card backdrop-blur ${className ?? ""}`}>
      <div className="flex items-center gap-2 text-xs font-medium text-text">
        <span className="grid size-6 place-items-center rounded-md bg-accent-soft text-accent-hi">
          <Sparkles className="size-3.5" />
        </span>
        {title}
      </div>
      <div className="mt-2 text-[11px] leading-4 text-muted">{body}</div>
    </div>
  );
}
