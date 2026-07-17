import Link from "next/link";
import { ArrowRight, Compass, Eye, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Logo } from "@/components/ui/logo";
import { Reveal } from "@/components/ui/reveal";

export const metadata = {
  title: "About — Saqua",
  description:
    "Why Saqua exists: the whole outbound pipeline a founder runs alone, built to show its work at every step.",
};

const PRINCIPLES = [
  {
    icon: Eye,
    title: "Show the work",
    body: "Every score, finding, and source is on screen. If Saqua can't point to a real signal for reaching out, it doesn't pretend to have one.",
  },
  {
    icon: ShieldCheck,
    title: "You own every send",
    body: "Nothing leaves your mailbox without you approving it. Saqua drafts, checks, and waits — the founder stays in the loop, always.",
  },
  {
    icon: Compass,
    title: "One founder, not a factory",
    body: "The whole thing is tuned to sound like a person who actually built the product — not a template farm spraying a purchased list.",
  },
];

export default function AboutPage() {
  return (
    <main className="relative min-h-screen overflow-hidden bg-bg text-text">
      {/* Ambient depth — same drifting blooms as the rest of the marketing site */}
      <div aria-hidden className="pointer-events-none absolute inset-0 -z-10">
        <div className="bloom-indigo animate-drift absolute -left-40 -top-40 size-[640px] rounded-full" />
        <div className="bloom-teal animate-drift-2 absolute -right-52 top-[36%] size-[580px] rounded-full" />
        <div className="bloom-indigo animate-drift absolute -bottom-52 left-[32%] size-[520px] rounded-full opacity-70" />
      </div>

      <header className="mx-auto flex h-16 max-w-5xl items-center justify-between px-6">
        <Link href="/" className="flex items-center gap-2 text-sm font-semibold">
          <Logo className="h-6 w-auto" /> Saqua
        </Link>
        <nav className="hidden items-center gap-7 text-xs text-muted md:flex">
          <Link href="/#pipeline" className="hover:text-text">How it works</Link>
          <Link href="/pricing" className="hover:text-text">Pricing</Link>
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
      <section className="mx-auto max-w-3xl px-6 pb-16 pt-12 text-center">
        <Reveal>
          <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-border bg-white/[0.03] px-3 py-1.5 text-xs text-text-2">
            <span className="size-1.5 animate-pulse-soft rounded-full bg-accent" />
            Why we built it
          </div>
          <h1 className="text-4xl font-semibold leading-[1.08] tracking-tight md:text-5xl">
            Outbound is a pipeline. Founders were sold a template.
          </h1>
          <p className="mx-auto mt-6 max-w-xl text-base leading-7 text-text-2">
            Most &ldquo;AI outreach&rdquo; tools automate the one step that was never the hard part —
            typing the email — and leave you to do the research, the qualifying, the follow-ups, and
            the reply-watching by hand. Saqua was built the other way around: run the entire machine
            an SDR would, and show its work at every stage, so a founder can do real outbound without
            hiring for it or pretending a mail-merge is personal.
          </p>
        </Reveal>
      </section>

      {/* ── Principles ───────────────────────────────────────────────── */}
      <section className="mx-auto max-w-5xl px-6 py-8">
        <div className="grid gap-4 md:grid-cols-3">
          {PRINCIPLES.map((p, i) => (
            <Reveal key={p.title} delay={i * 0.07}>
              <div className="group glass hover-lift h-full rounded-xl border border-border p-5 shadow-card">
                <span className="grid size-10 place-items-center rounded-md bg-accent-soft text-accent-hi transition-transform duration-300 ease-smooth group-hover:scale-110">
                  <p.icon className="size-5" />
                </span>
                <div className="mt-4 font-medium text-text">{p.title}</div>
                <p className="mt-2 text-sm leading-6 text-text-2">{p.body}</p>
              </div>
            </Reveal>
          ))}
        </div>
      </section>

      {/* ── The one serif moment, echoing the landing page ───────────── */}
      <section className="mx-auto max-w-3xl px-6 py-12">
        <Reveal>
          <div className="glass relative overflow-hidden rounded-2xl border border-border p-8 shadow-card md:p-10">
            <p className="text-sm leading-6 text-text-2">
              Saqua is a small, deliberate product from a founder who got tired of choosing between
              generic agency spam and spending every morning on prospecting. It&apos;s early, it&apos;s
              honest about what it can and can&apos;t do yet, and it&apos;s built to earn the reply —
              one real message at a time.
            </p>
            {/* Newsreader appears here for the same reason it does everywhere else in the
                app: this is a line of the kind of copy Saqua itself writes. */}
            <p className="mt-5 border-t border-border-faint pt-5 font-serif text-[17px] leading-8 text-text">
              &ldquo;If it can&apos;t find a real reason to reach out, it shouldn&apos;t reach out.&rdquo;
            </p>
          </div>
        </Reveal>
      </section>

      {/* ── CTA ──────────────────────────────────────────────────────── */}
      <section className="mx-auto max-w-3xl px-6 py-12 text-center">
        <Reveal>
          <h2 className="text-2xl font-semibold tracking-tight md:text-3xl">
            Run your own pipeline.
          </h2>
          <p className="mx-auto mt-3 max-w-md text-sm leading-6 text-text-2">
            Start free and watch Saqua research, score, and draft a real opener before you pay a cent.
          </p>
          <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
            <Button asChild variant="primary" size="lg">
              <Link href="/sign-up">
                Get started for free <ArrowRight className="size-4" />
              </Link>
            </Button>
            <Button asChild variant="secondary" size="lg">
              <Link href="/pricing">See pricing</Link>
            </Button>
          </div>
        </Reveal>
      </section>

      <footer className="mx-auto max-w-5xl px-6 py-10">
        <div className="flex flex-col items-center justify-between gap-4 border-t border-border-faint pt-8 text-xs text-muted sm:flex-row">
          <div className="flex items-center gap-2 text-text-2">
            <Logo className="h-5 w-auto" />
            Saqua
          </div>
          <div className="flex items-center gap-6">
            <Link href="/pricing" className="hover:text-text">Pricing</Link>
            <Link href="/privacy" className="hover:text-text">Privacy</Link>
            <Link href="/terms" className="hover:text-text">Terms</Link>
          </div>
        </div>
      </footer>
    </main>
  );
}
