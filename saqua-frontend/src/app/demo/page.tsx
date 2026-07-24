import type { Metadata } from "next";
import { Sparkles } from "lucide-react";
import { SiteNav } from "@/components/marketing/site-nav";
import { SiteFooter } from "@/components/marketing/site-footer";
import { DemoGate } from "@/components/demo/demo-gate";

export const metadata: Metadata = {
  title: "Live demo — Saqua",
  description:
    "Step into the real Saqua workspace on live data: chat, prospects, and settings, running the real research + scoring + guard-checked drafting pipeline. No account needed.",
};

export default function DemoPage() {
  return (
    // No bg here: it would paint over the -z-10 glow (see globals.css .page-light).
    <main className="relative min-h-screen overflow-clip text-text">
      <div aria-hidden className="hero-glow-cool pointer-events-none absolute inset-x-0 top-0 -z-10 h-[560px]" />

      <SiteNav />

      <section className="px-6 pb-24 pt-32 lg:px-12">
        <div className="mx-auto max-w-2xl text-center">
          <span className="float-soft inline-flex h-8 items-center gap-2 rounded-full border border-border bg-white px-4 text-xs font-medium shadow-[0_1px_2px_rgba(17,17,17,.04)]">
            <Sparkles className="size-3.5 text-accent" /> Live, on real data — no account
          </span>
          <h1 className="mx-auto mt-7 max-w-[16ch] font-display text-4xl font-medium leading-[1.05] tracking-[-0.03em] md:text-6xl">
            Step inside the <span className="grad-text-anim">real product</span>.
          </h1>
          <p className="mx-auto mt-5 max-w-xl text-base leading-7 text-muted">
            Not a mockup — the actual Saqua workspace, on a sandbox. Move between chat, prospects,
            and settings the way a customer does, with the real research, scoring, and guard-checked
            drafting running live underneath.
          </p>
        </div>

        <div className="mt-10">
          <DemoGate />
        </div>
      </section>

      <SiteFooter />
    </main>
  );
}
