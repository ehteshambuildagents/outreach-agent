import Link from "next/link";
import { ArrowRight, Compass, Eye, ShieldCheck, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Reveal } from "@/components/ui/reveal";
import { SiteNav } from "@/components/marketing/site-nav";
import { SiteFooter } from "@/components/marketing/site-footer";
import { PRELAUNCH, WAITLIST_ANCHOR } from "@/lib/launch";

export const metadata = {
  title: "About · Saqua",
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
    body: "Nothing leaves your mailbox without you approving it. Saqua drafts, checks, and waits, so the founder stays in the loop, always.",
  },
  {
    icon: Compass,
    title: "One founder, not a factory",
    body: "The whole thing is tuned to sound like a person who actually built the product, not a template farm spraying a purchased list.",
  },
];

export default function AboutPage() {
  return (
    // No bg here: it would paint over the -z-10 glow (see globals.css .page-light).
    <main className="relative min-h-screen overflow-clip text-text">
      <div aria-hidden className="hero-glow-cool pointer-events-none absolute inset-x-0 top-0 -z-10 h-[620px]" />

      {/* Decorative drifting blooms — pure CSS, ambient depth behind the hero. */}
      <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[620px] overflow-clip">
        <div className="bloom-indigo drift absolute -left-24 top-24 h-72 w-72 rounded-full" />
        <div className="bloom-teal drift-2 absolute right-[-60px] top-10 h-80 w-80 rounded-full" />
      </div>

      <SiteNav />

      {/* ── Hero ─────────────────────────────────────────────────────── */}
      <section className="mx-auto max-w-3xl px-6 pb-16 pt-36 text-center">
        <Reveal>
          <span className="float-soft inline-flex h-8 items-center gap-2 rounded-full border border-border bg-white px-4 text-xs font-medium shadow-[0_1px_2px_rgba(17,17,17,.04)]">
            <Sparkles className="size-3.5 text-accent" /> Why we built it
          </span>
          <h1 className="mx-auto mt-7 max-w-[20ch] font-display text-4xl font-medium leading-[1.05] tracking-[-0.03em] md:text-6xl">
            Outbound is a pipeline. Founders were sold a <span className="grad-text-anim">template</span>.
          </h1>
          <p className="mx-auto mt-6 max-w-xl text-base leading-7 text-muted">
            Most &ldquo;AI outreach&rdquo; tools automate the one step that was never the hard part,
            typing the email, and leave you to do the research, the qualifying, the follow-ups, and
            the reply-watching by hand. Saqua was built the other way around: it runs the entire
            machine an SDR would, and shows its work at every stage.
          </p>
        </Reveal>
      </section>

      {/* ── Principles ───────────────────────────────────────────────── */}
      <section className="mx-auto max-w-5xl px-6 py-8">
        <div className="grid gap-5 md:grid-cols-3">
          {PRINCIPLES.map((p, i) => (
            <Reveal key={p.title} delay={i * 0.09}>
              <div className="group glass hover-lift h-full rounded-2xl border border-border p-7 shadow-card">
                <span className="grid size-12 place-items-center rounded-full bg-accent-soft text-accent transition-transform duration-300 ease-smooth group-hover:-rotate-6 group-hover:scale-110">
                  <p.icon className="size-5" />
                </span>
                <div className="mt-5 text-lg font-semibold text-text">{p.title}</div>
                <p className="mt-2 text-sm leading-6 text-text-2">{p.body}</p>
              </div>
            </Reveal>
          ))}
        </div>
      </section>

      {/* ── The one serif moment — a short letter ────────────────────── */}
      <section className="mx-auto max-w-3xl px-6 py-12">
        <Reveal>
          <div className="glass relative overflow-hidden rounded-2xl border border-border p-8 shadow-card md:p-10">
            <p className="text-sm leading-6 text-text-2">
              Saqua is a small, deliberate product from a founder who got tired of choosing between
              generic agency spam and spending every morning on prospecting. It&apos;s early, it&apos;s
              honest about what it can and can&apos;t do yet, and it&apos;s built to earn the reply,
              one real message at a time.
            </p>
            <p className="mt-5 border-t border-border-faint pt-5 font-serif text-[19px] leading-8 text-text">
              &ldquo;If it can&apos;t find a real reason to reach out, it shouldn&apos;t reach out.&rdquo;
            </p>
          </div>
        </Reveal>
      </section>

      {/* ── CTA ──────────────────────────────────────────────────────── */}
      <section className="mx-auto max-w-3xl px-6 py-12 text-center">
        <Reveal>
          <h2 className="font-display text-3xl font-semibold tracking-tight md:text-4xl">
            Run your own <span className="grad-text-anim">pipeline</span>.
          </h2>
          <p className="mx-auto mt-3 max-w-md text-sm leading-6 text-muted">
            {PRELAUNCH
              ? "Join the waitlist and we will email you the moment Saqua opens, with founding pricing locked in."
              : "Start free and watch Saqua research, score, and draft a real opener before you pay a cent."}
          </p>
          <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
            <Button asChild variant="primary" size="lg">
              <Link href={PRELAUNCH ? WAITLIST_ANCHOR : "/sign-up"}>
                {PRELAUNCH ? "Join the waitlist" : "Get started for free"}{" "}
                <ArrowRight className="size-4" />
              </Link>
            </Button>
            <Button asChild variant="secondary" size="lg" className="rounded-full">
              <Link href="/pricing">See pricing</Link>
            </Button>
          </div>
        </Reveal>
      </section>

      <SiteFooter />
    </main>
  );
}
