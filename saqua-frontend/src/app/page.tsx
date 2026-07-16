import Link from "next/link";
import {
  ArrowRight,
  CalendarClock,
  Gauge,
  MailCheck,
  PenLine,
  Search,
  ShieldCheck,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { BrowserFrame } from "@/components/ui/browser-frame";
import { Reveal } from "@/components/ui/reveal";

const STAGES = [
  {
    n: "01",
    icon: Search,
    title: "Research",
    body: "Reads hiring posts, funding news, and public activity to find a real reason to reach out — not a name off a purchased list.",
  },
  {
    n: "02",
    icon: Gauge,
    title: "Score & qualify",
    body: "Every prospect gets a fit score and a qualification pass before a word is written. Low-fit companies get filtered out, not spammed.",
  },
  {
    n: "03",
    icon: PenLine,
    title: "Write",
    body: "The opening email is built around the specific evidence found in research, then tested against the patterns that make outreach read as AI-generated — before you ever see it.",
  },
  {
    n: "04",
    icon: CalendarClock,
    title: "Send & follow up",
    body: "Once you approve, it sends and runs a multi-touch cadence — a sequence of spaced follow-ups — over real days, with timing re-anchored to the actual send so it never compresses into something that looks automated.",
  },
  {
    n: "05",
    icon: MailCheck,
    title: "Detect replies",
    body: "The moment a real reply lands in the thread, every remaining step is cancelled automatically. That's a core guarantee, not an edge case.",
  },
];

export default function LandingPage() {
  return (
    <main className="relative min-h-screen overflow-hidden bg-bg text-text">
      {/* Ambient depth — muted indigo + teal blooms, off-center, low opacity */}
      <div aria-hidden className="pointer-events-none absolute inset-0 -z-10">
        <div className="bloom-indigo absolute -left-40 -top-40 size-[680px] rounded-full" />
        <div className="bloom-teal absolute -right-52 top-[38%] size-[620px] rounded-full" />
        <div className="bloom-indigo absolute -bottom-52 left-[30%] size-[560px] rounded-full opacity-70" />
      </div>

      <header className="mx-auto flex h-16 max-w-7xl items-center justify-between px-6">
        <Link href="/" className="flex items-center gap-2 text-sm font-semibold">
          <span className="grid size-7 place-items-center rounded-md bg-accent text-[color:var(--accent-ink)]">
            <ShieldCheck className="size-4" />
          </span>
          Saqua
        </Link>
        <nav className="hidden items-center gap-7 text-xs text-muted md:flex">
          <a href="#pipeline" className="hover:text-text">How it works</a>
          <Link href="/pricing" className="hover:text-text">Pricing</Link>
          <Link href="/about" className="hover:text-text">About</Link>
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

      {/* ── Hero ─────────────────────────────────────────────────────── */}
      <section className="mx-auto grid max-w-7xl items-center gap-14 px-6 pb-24 pt-10 lg:grid-cols-[1fr_540px] lg:pt-16">
        <div className="max-w-2xl">
          <div className="mb-7 inline-flex items-center gap-2 rounded-full border border-border bg-white/[0.03] px-3 py-1.5 text-xs text-text-2">
            <span className="size-1.5 animate-pulse-soft rounded-full bg-accent" />
            Research to reply — run by you
          </div>
          <h1 className="text-5xl font-semibold leading-[1.04] tracking-tight text-text md:text-6xl">
            Run your own outbound pipeline, not just the emails.
          </h1>
          <p className="mt-6 max-w-xl text-base leading-7 text-text-2">
            Saqua is the whole outbound machine a founder runs instead of hiring an SDR — the
            person who does sales prospecting — or paying an agency for generic templates. It
            researches real signals, scores fit, writes the opener around the evidence, sends and
            follows up over real days, and stops the instant someone replies.
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-3">
            <Button asChild variant="primary" size="lg">
              <Link href="/sign-up">
                Get started for free <ArrowRight className="size-4" />
              </Link>
            </Button>
            <Button asChild variant="secondary" size="lg">
              <a href="https://cal.com/saqua/demo-call" target="_blank" rel="noopener noreferrer">
                Book a demo
              </a>
            </Button>
          </div>
          <p className="mt-6 text-xs text-muted">
            No credit card required. Every draft is checked against hundreds of automated tests
            before it reaches you.
          </p>
        </div>

        {/* Live product moment — a researched, scored prospect at saqua.io/research */}
        <Reveal className="lg:justify-self-end" y={20}>
          <BrowserFrame url="saqua.io/research" className="w-full">
            <div className="space-y-4 p-5">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-sm font-medium text-text">Linear</div>
                  <div className="text-xs text-muted">linear.app · issue tracking for software teams</div>
                </div>
                <div className="flex items-center gap-1.5 rounded-md border border-accent-line bg-accent-soft px-2 py-1 text-accent-hi">
                  <span className="font-mono text-sm font-semibold leading-none">92</span>
                  <span className="text-[10px] uppercase tracking-wide">fit</span>
                </div>
              </div>
              <div className="space-y-2">
                {[
                  ["Hiring", "Posted 2 GTM roles in the last 30 days"],
                  ["Funding", "Series B, 8 months ago — expanding motion"],
                  ["Signal", "Founder active on launches; ships weekly"],
                ].map(([k, v]) => (
                  <div key={k} className="flex gap-3 rounded-md border border-border-faint bg-white/[0.02] px-3 py-2">
                    <span className="w-16 shrink-0 font-mono text-[10px] uppercase tracking-wide text-muted">{k}</span>
                    <span className="text-xs leading-5 text-text-2">{v}</span>
                  </div>
                ))}
              </div>
              <div className="flex items-center justify-between border-t border-border-faint pt-3">
                <span className="inline-flex items-center gap-1.5 text-xs text-accent-hi">
                  <span className="size-1.5 rounded-full bg-accent" /> Qualified — worth pursuing
                </span>
                <span className="font-mono text-[10px] text-muted">3 sources</span>
              </div>
            </div>
          </BrowserFrame>
        </Reveal>
      </section>

      {/* ── The pipeline: five stages ────────────────────────────────── */}
      <section id="pipeline" className="mx-auto max-w-7xl px-6 py-20">
        <Reveal>
          <h2 className="max-w-2xl text-3xl font-semibold tracking-tight">
            One pipeline. Five stages. All of it yours.
          </h2>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-text-2">
            This is the part most tools skip. Saqua doesn&apos;t just write — it runs every step an
            SDR would, and shows its work at each one.
          </p>
        </Reveal>
        <div className="mt-10 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {STAGES.map((s, i) => (
            <Reveal key={s.n} delay={i * 0.06}>
              <div className="h-full rounded-xl border border-border bg-card p-5 transition-colors hover:border-border-strong">
                <div className="mb-5 flex items-center justify-between">
                  <span className="grid size-10 place-items-center rounded-md bg-accent-soft text-accent-hi">
                    <s.icon className="size-5" />
                  </span>
                  <span className="font-mono text-xs text-faint">{s.n}</span>
                </div>
                <div className="font-medium text-text">{s.title}</div>
                <p className="mt-2 text-sm leading-6 text-text-2">{s.body}</p>
              </div>
            </Reveal>
          ))}
        </div>
      </section>

      {/* ── Compose: the one place serif appears (generated email copy) ── */}
      <section className="mx-auto max-w-7xl px-6 py-20">
        <div className="grid items-center gap-12 lg:grid-cols-[1fr_1.1fr]">
          <Reveal>
            <h2 className="max-w-md text-3xl font-semibold tracking-tight">
              The opener is written around the evidence — and tested before you see it.
            </h2>
            <p className="mt-4 max-w-md text-sm leading-6 text-text-2">
              No mail-merge templates. Each first email is built from what research actually found,
              then run against hundreds of checks for the tells that give AI outreach away. You
              approve what sends.
            </p>
            <ul className="mt-6 space-y-2.5">
              {[
                "Grounded in the specific signal, not a variable swap",
                "Checked for AI-sounding patterns before it's shown",
                "You approve every send — nothing goes out on its own",
              ].map((t) => (
                <li key={t} className="flex items-start gap-2.5 text-sm text-text-2">
                  <MailCheck className="mt-0.5 size-4 shrink-0 text-accent-hi" /> {t}
                </li>
              ))}
            </ul>
          </Reveal>

          <Reveal delay={0.1} y={20}>
            <BrowserFrame url="saqua.io/compose">
              <div className="p-5">
                <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
                  <span className="inline-flex items-center gap-1.5 rounded-md border border-accent-line bg-accent-soft px-2 py-1 text-accent-hi">
                    <ShieldCheck className="size-3" /> Passed 214 checks
                  </span>
                  <span className="font-mono text-muted">to: karri@linear.app</span>
                  <span className="ml-auto font-mono text-muted">fit 92</span>
                </div>
                <div className="rounded-lg border border-border-faint bg-white/[0.02] p-4">
                  <div className="mb-2 text-sm font-medium text-text">
                    Quick one on your GTM hiring
                  </div>
                  {/* The single serif treatment across the whole product: generated email copy */}
                  <p className="font-serif text-[15px] leading-7 text-text">
                    Saw you opened two GTM roles this month, right after the Series B. Usually that
                    means the founder is still the one running outbound and feeling it. I built Saqua
                    for exactly that stretch — it researches, writes, and follows up so you can stay
                    on the product. Worth fifteen minutes?
                  </p>
                </div>
              </div>
            </BrowserFrame>
          </Reveal>
        </div>
      </section>

      {/* ── The reply guarantee — the single strongest live signal ─────── */}
      <section className="mx-auto max-w-7xl px-6 py-16">
        <Reveal>
          <div className="relative overflow-hidden rounded-2xl border border-accent-line bg-card p-8 md:p-12">
            <div className="accent-glow absolute -right-20 -top-20 size-80 opacity-60" />
            <div className="relative flex flex-col items-start gap-4 md:flex-row md:items-center md:justify-between">
              <div className="max-w-xl">
                <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-accent-line bg-accent-soft px-3 py-1 text-xs text-accent-hi">
                  <span className="size-1.5 animate-pulse-soft rounded-full bg-accent" /> Live guarantee
                </div>
                <h2 className="text-2xl font-semibold tracking-tight md:text-3xl">
                  When they reply, the sequence stops. Automatically.
                </h2>
                <p className="mt-3 text-sm leading-6 text-text-2">
                  No one gets a &ldquo;just following up&rdquo; after they&apos;ve already answered.
                  Reply detection is verified with real tests, not assumed to work.
                </p>
              </div>
              <Button asChild variant="primary" size="lg">
                <Link href="/sign-up">
                  Start your pipeline <ArrowRight className="size-4" />
                </Link>
              </Button>
            </div>
          </div>
        </Reveal>
      </section>

      <footer className="mx-auto max-w-7xl px-6 py-10">
        <div className="flex flex-col items-center justify-between gap-4 border-t border-border-faint pt-8 text-xs text-muted sm:flex-row">
          <div className="flex items-center gap-2 text-text-2">
            <span className="grid size-6 place-items-center rounded bg-accent text-[color:var(--accent-ink)]">
              <ShieldCheck className="size-3.5" />
            </span>
            Saqua
          </div>
          <div className="flex items-center gap-6">
            <Link href="/pricing" className="hover:text-text">Pricing</Link>
            <Link href="/about" className="hover:text-text">About</Link>
            <Link href="/privacy" className="hover:text-text">Privacy</Link>
            <Link href="/terms" className="hover:text-text">Terms</Link>
          </div>
        </div>
      </footer>
    </main>
  );
}
