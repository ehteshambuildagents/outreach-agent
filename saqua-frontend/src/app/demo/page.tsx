import type { Metadata } from "next";
import { Sparkles } from "lucide-react";
import { SiteNav } from "@/components/marketing/site-nav";
import { SiteFooter } from "@/components/marketing/site-footer";
import { DemoConsole } from "@/components/marketing/demo-console";

export const metadata: Metadata = {
  title: "Live demo — Saqua",
  description:
    "Watch Saqua's real pipeline run: it finds companies that match your ICP, researches and scores each for fit, and drafts an evidence-anchored opener for the best match. No account needed.",
};

export default function DemoPage() {
  return (
    // No bg here: it would paint over the -z-10 glow (see globals.css .page-light).
    <main className="relative min-h-screen overflow-clip text-text">
      <div aria-hidden className="hero-glow-cool pointer-events-none absolute inset-x-0 top-0 -z-10 h-[560px]" />

      <SiteNav />

      <section className="px-6 pb-24 pt-36 lg:px-12">
        <div className="mx-auto max-w-2xl text-center">
          <span className="float-soft inline-flex h-8 items-center gap-2 rounded-full border border-border bg-white px-4 text-xs font-medium shadow-[0_1px_2px_rgba(17,17,17,.04)]">
            <Sparkles className="size-3.5 text-accent" /> Live, on real data — no account
          </span>
          <h1 className="mx-auto mt-7 max-w-[16ch] font-display text-4xl font-medium leading-[1.05] tracking-[-0.03em] md:text-6xl">
            See Saqua <span className="grad-text-anim">qualify</span> real prospects.
          </h1>
          <p className="mx-auto mt-5 max-w-xl text-base leading-7 text-muted">
            Tell it who you sell to. It finds companies that match, researches and scores each one
            for fit, then drafts the opener around the real signal it found — live, the same pipeline
            the product runs.
          </p>
        </div>

        <div className="mt-10">
          <DemoConsole />
        </div>
      </section>

      <SiteFooter />
    </main>
  );
}
