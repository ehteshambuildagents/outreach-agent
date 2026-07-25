import type { Metadata } from "next";
import { Check } from "lucide-react";
import { SiteNav } from "@/components/marketing/site-nav";
import { SiteFooter } from "@/components/marketing/site-footer";
import { DemoGate } from "@/components/demo/demo-gate";
import { WorkspacePreview } from "@/components/demo/workspace-preview";
import { Reveal } from "@/components/ui/reveal";

export const metadata: Metadata = {
  title: "Live demo · Saqua",
  description:
    "Try Saqua live: research real prospects, score them, and generate evidence-backed outreach in a live interactive demo. No account required.",
};

/**
 * The demo's front door, designed as the THRESHOLD of the product rather than
 * an email-collection page: a strong "what you're about to experience" hero,
 * the workspace itself previewed underneath, and the Gmail step as one small
 * card overlapping the preview. The gate handles the staged transition into
 * the real /ai workspace.
 */

// The three honest facts, kept to one quiet line each. Everything longer lives
// in the preview itself.
const META = [
  "Runs the real research pipeline",
  "No account required",
  "Sending paused during Google verification",
];

export default function DemoPage() {
  return (
    // No bg here: it would paint over the -z-10 glow (see globals.css .page-light).
    <main className="relative min-h-screen overflow-clip text-text">
      <div aria-hidden className="pointer-events-none absolute inset-0 -z-10">
        <div className="hero-glow-cool absolute inset-x-0 top-0 h-[620px]" />
        <div className="bloom-indigo animate-drift absolute -left-40 top-[38%] size-[520px] rounded-full opacity-60" />
        <div className="bloom-teal animate-drift-2 absolute -right-52 top-[62%] size-[520px] rounded-full opacity-60" />
      </div>

      <SiteNav />

      {/* ── Hero: what am I about to experience? ─────────────────────── */}
      <section className="px-6 pt-24 text-center lg:px-12">
        <div className="mx-auto max-w-3xl">
          <Reveal>
            <span className="inline-flex h-8 items-center gap-2.5 rounded-full border border-border bg-white px-4 text-xs font-medium shadow-[0_1px_2px_rgba(17,17,17,.04)]">
              <span className="relative flex size-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[color:var(--success)] opacity-60 motion-reduce:animate-none" />
                <span className="relative inline-flex size-2 rounded-full bg-[color:var(--success)]" />
              </span>
              Live interactive demo
            </span>
          </Reveal>
          <Reveal delay={0.05}>
            <h1 className="mx-auto mt-7 font-display text-5xl font-medium leading-[1.03] tracking-[-0.03em] md:text-7xl">
              Try <span className="grad-text-anim">Saqua</span> live.
            </h1>
          </Reveal>
          <Reveal delay={0.1}>
            <p className="mx-auto mt-6 max-w-2xl text-lg leading-8 text-muted">
              Research real prospects, score them, and generate evidence-backed outreach,
              live in the workspace we&apos;re launching.
            </p>
          </Reveal>
          <Reveal delay={0.15}>
            <ul className="mx-auto mt-8 flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-sm text-text-2">
              {META.map((m) => (
                <li key={m} className="inline-flex items-center gap-1.5">
                  <Check className="size-3.5 shrink-0 text-accent" />
                  {m}
                </li>
              ))}
            </ul>
          </Reveal>
        </div>
      </section>

      {/* ── The ask, immediately after the hero ─────────────────────────
             This is the page's primary conversion point, so it sits directly
             under the trust bullets: a visitor finishes reading what Saqua does
             and the very next thing is the one field that gets them in. The
             preview below is supporting evidence, not a gate to scroll past. */}
      <section className="px-6 pt-10 lg:px-12">
        <Reveal delay={0.18}>
          <div className="relative mx-auto max-w-md">
            {/* A soft accent bloom behind the card lifts it off the canvas as
                the focal point without adding any borders or shouting. */}
            <div
              aria-hidden
              className="accent-glow pointer-events-none absolute -inset-10 -z-10 rounded-[32px]"
            />
            <DemoGate />
          </div>
        </Reveal>
      </section>

      {/* ── Supporting evidence: the workspace they are about to enter ── */}
      <section className="px-6 pb-20 pt-14 lg:px-12">
        <Reveal delay={0.05} y={22}>
          <WorkspacePreview />
        </Reveal>
        <Reveal delay={0.1}>
          <p className="mx-auto mt-5 max-w-xl text-center text-sm leading-6 text-muted">
            A preview of the workspace you&apos;ll enter. Research, fit scoring, and
            guard-checked drafts all run for real on live data.
          </p>
        </Reveal>
      </section>

      <SiteFooter />
    </main>
  );
}
