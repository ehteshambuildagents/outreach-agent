import {
  Zap,
  BadgePercent,
  Rocket,
  Users,
  Briefcase,
  Sparkles,
  Waves,
  Layers,
  Check,
  X,
} from "lucide-react";
import { Reveal } from "@/components/ui/reveal";
import { SiteNav } from "@/components/marketing/site-nav";
import { SiteFooter } from "@/components/marketing/site-footer";
import { HeroForm } from "@/components/marketing/hero-form";
import { FeatureTabs } from "@/components/marketing/feature-tabs";

const AUDIENCES = [
  {
    icon: Rocket,
    title: "Founders",
    body: "You sell best in your own voice. Saqua does the research and the first draft, so every intro sounds like you wrote it, at far more than one-at-a-time speed.",
  },
  {
    icon: Users,
    title: "Lean GTM teams",
    body: "Replace the sequence tool and the enrichment tab. Research, drafting, follow-ups, and reply detection live in one workspace your whole team works out of.",
  },
  {
    icon: Briefcase,
    title: "Agencies",
    body: "Run personal outbound for every client without cloning yourself. Saqua keeps each account's voice, offer, and ICP straight.",
  },
];

// The before/after worksheet. Kept to four beats a side so the two columns stay
// the same height without hand-tuning.
const PAIN = [
  "Spend hours hunting for companies actually worth contacting",
  "Send templates that read automated and get quietly ignored",
  "Lose deals to follow-ups you meant to send and never did",
  "Ride the feast or famine revenue roller coaster",
];

const GAIN = [
  "Fresh, high fit prospects scored and waiting every morning",
  "Every opener grounded in a real signal, with the quote behind it",
  "Follow-ups that run on real days and stop the moment someone replies",
  "A repeatable research to reply system you can run every week",
];

const DIFFERENTIATORS = [
  {
    icon: Sparkles,
    title: "Grounded, never guessed",
    body: "Every claim carries the exact quote it came from. Generic AI invents facts; one wrong detail and the trust is gone. Saqua leaves it out instead.",
  },
  {
    icon: Waves,
    title: "Your voice, kept",
    body: "Short, specific, human. Written to sound like a founder, not a sequence. It refuses to send anything that reads automated.",
  },
  {
    icon: Layers,
    title: "One workspace",
    body: "Research, discovery, drafting, pipeline, inbox, and follow-ups in one place. Replace the enrichment tab, the template doc, and the sequence tool.",
  },
];

export default function LandingPage() {
  return (
    // No bg here on purpose: an opaque background on this (positioned, z-index:auto,
    // so NOT a stacking context) element would paint over the -z-10 lights below.
    // html/body already paint the same canvas.
    <main className="relative min-h-screen overflow-clip text-text">
      {/* One warm light bleeding in from the top of the page and its mirror at the
          bottom — both pushed far enough past the edge that only the falloff lands
          on the canvas. Cool blooms drift between them for depth. */}
      <div aria-hidden className="pointer-events-none absolute inset-0 -z-10">
        <div className="page-light absolute inset-x-0 -top-[461px] h-[806px]" />
        <div className="page-light absolute inset-x-0 -bottom-[461px] h-[806px]" />
        <div className="bloom-indigo animate-drift absolute -left-40 top-[52%] size-[560px] rounded-full opacity-70" />
        <div className="bloom-teal animate-drift-2 absolute -right-52 top-[74%] size-[560px] rounded-full opacity-70" />
      </div>

      <SiteNav />

      {/* ── Hero ─────────────────────────────────────────────────────── */}
      <section className="px-6 pb-20 pt-36 text-center lg:px-12">
        <Reveal>
          <span className="inline-flex h-8 items-center gap-2 rounded-full border border-border bg-white px-4 text-xs font-medium shadow-[0_1px_2px_rgba(17,17,17,.04)]">
            <Zap className="size-3.5 text-accent" /> Research to reply, run by you
          </span>
        </Reveal>
        <Reveal delay={0.05}>
          <h1 className="mx-auto mt-7 max-w-[18ch] font-display text-5xl font-medium leading-[1.03] tracking-[-0.03em] md:text-7xl">
            The whole outbound pipeline,<br />without the <span className="grad-text-anim">headcount</span>.
          </h1>
        </Reveal>
        <Reveal delay={0.1}>
          <p className="mx-auto mt-6 max-w-2xl text-lg leading-8 text-muted">
            Saqua is the AI SDR a founder runs instead of hiring one. It finds the right companies,
            researches real signals, writes the opener around the evidence, follows up over real
            days, and stops the instant someone replies.
          </p>
        </Reveal>

        <Reveal delay={0.15}>
          {/* Two CTAs: the live demo is the PRIMARY action (visitors from PH/X/
              LinkedIn should feel the product first); the waitlist is the quieter
              second path. The full waitlist form lives in the #waitlist section. */}
          <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <a
              href="/demo"
              className="inline-flex h-12 w-full items-center justify-center gap-2 rounded-full bg-accent px-7 text-sm font-semibold text-white shadow-[0_1px_2px_rgba(79,90,247,.35)] transition-all hover:-translate-y-px hover:bg-accent-hi hover:shadow-[0_8px_22px_rgba(79,90,247,.32)] sm:w-auto"
            >
              Try the live demo <Sparkles className="size-4" />
            </a>
            <a
              href="#waitlist"
              className="inline-flex h-12 w-full items-center justify-center gap-2 rounded-full border border-border-strong bg-white px-7 text-sm font-semibold text-text shadow-[0_1px_2px_rgba(17,17,17,.06)] transition-all hover:-translate-y-px hover:border-accent-line hover:text-accent hover:shadow-[0_8px_22px_rgba(79,90,247,.16)] sm:w-auto"
            >
              Join the waitlist
            </a>
          </div>
          <p className="mt-4 text-xs text-muted">
            The demo runs the real pipeline on live data. No account, just a personal Gmail.{" "}
            <a href="https://cal.com/saqua/demo-call" target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">
              Or book a call
            </a>
            .
          </p>
        </Reveal>

        {/* Founding offer: no countdown, no manufactured urgency. The one real
            condition (be on the waitlist before launch) is stated plainly. */}
        <Reveal delay={0.2} className="mx-auto mt-10 max-w-sm">
          <div className="grad-text font-display text-lg font-semibold">
            Start free with founding pricing, locked for life
          </div>
          <div className="mt-3 rounded-2xl border border-border bg-card p-6 text-center shadow-card">
            <div className="mx-auto mb-3 grid size-10 place-items-center rounded-full bg-indigo-soft text-accent">
              <BadgePercent className="size-5" />
            </div>
            <div>
              <span className="font-display text-lg font-semibold text-text">40% off</span>{" "}
              <span className="text-muted">for life</span>
            </div>
            <p className="mt-4 text-xs leading-5 text-muted">
              For everyone on the waitlist before launch day. Early access stays
              limited while Gmail verification is in progress.
            </p>
          </div>
        </Reveal>

        {/* Trust — 30 countries */}
        <Reveal delay={0.25}>
          <div className="mt-8 flex items-center justify-center gap-3 text-sm text-muted">
            <div className="flex -space-x-2">
              {["from-accent to-[#8b93ff]", "from-[#6b74ff] to-[#c3c8ff]", "from-[#8b93ff] to-accent", "from-[#4453e8] to-[#6b74ff]"].map((g, i) => (
                <span key={i} className={`size-7 rounded-full border-2 border-bg bg-gradient-to-br ${g}`} />
              ))}
            </div>
            Trusted by founders in <span className="font-medium text-text">30 countries</span>
          </div>
        </Reveal>
      </section>

      {/* ── Before / after ───────────────────────────────────────────── */}
      <section className="relative px-6 py-16 lg:px-12">
        {/* The mark sunk into the canvas. Filled a hair darker than the page and
            faded out down its length, so it reads as embossed rather than drawn.
            -z-10 puts it in the same layer as the page lights; it sits later in
            the DOM, so it paints just above them. */}
        <svg
          aria-hidden
          viewBox="0 0 96 56"
          className="pointer-events-none absolute -left-40 -top-48 -z-10 w-[1200px] max-w-none"
        >
          <defs>
            <linearGradient id="saqua-emboss" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="#efecea" stopOpacity="1" />
              <stop offset="1" stopColor="#efecea" stopOpacity="0.3" />
            </linearGradient>
          </defs>
          <path fill="url(#saqua-emboss)" d="M9 40C24 31 38 30 52 20C62 13 75 13 86 21C76 17 64 19 53 26C40 35 26 38 12 44Z" />
          <path fill="url(#saqua-emboss)" d="M56 22C71 12 89 20 85 37C82 50 66 53 57 45C67 49 78 44 78 34C78 25 66 26 58 31Z" />
        </svg>

        <Reveal className="mx-auto mb-10 max-w-3xl text-center">
          <h2 className="font-display text-3xl font-semibold tracking-tight md:text-5xl">
            Tabs, templates, and &ldquo;I&apos;ll follow up later&rdquo;<br />
            is not a <span className="grad-text">sales system</span>.
          </h2>
          <p className="mx-auto mt-4 max-w-xl text-base leading-7 text-muted">
            Fragmented tools push you between a lead list, an enrichment tab, a template
            doc, and your inbox, so follow-ups get missed and revenue stays lumpy.
          </p>
        </Reveal>

        <div className="mx-auto grid max-w-5xl gap-5 md:grid-cols-2">
          <Reveal>
            <div className="h-full rounded-2xl border border-border bg-card/60 p-8 shadow-card">
              <h3 className="text-sm font-semibold uppercase tracking-wide text-muted">
                Right now
              </h3>
              <ul className="mt-6 space-y-4">
                {PAIN.map((item) => (
                  <li key={item} className="flex gap-3 text-sm leading-6 text-text-2">
                    <span className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full bg-black/[0.06] text-muted">
                      <X className="size-3" />
                    </span>
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          </Reveal>

          {/* The payoff column, lifted off the page by a warm bloom behind it. */}
          <Reveal delay={0.07}>
            <div className="relative h-full">
              <div
                aria-hidden
                className="bloom-teal pointer-events-none absolute -inset-16 -z-10 rounded-full"
              />
              <div className="glass h-full rounded-2xl border border-border p-8 shadow-card">
                <h3 className="text-sm font-semibold uppercase tracking-wide text-accent">
                  With Saqua
                </h3>
                <ul className="mt-6 space-y-4">
                  {GAIN.map((item) => (
                    <li key={item} className="flex gap-3 text-sm leading-6 text-text-2">
                      <span className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full bg-accent-soft text-accent">
                        <Check className="size-3" />
                      </span>
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </Reveal>
        </div>
      </section>

      {/* ── Feature dashboard (tab switcher) ─────────────────────────── */}
      <section className="px-6 py-16 lg:px-12">
        <Reveal className="mx-auto mb-10 max-w-2xl text-center">
          <span className="text-sm font-semibold text-accent">One workspace</span>
          <h2 className="mt-3 font-display text-3xl font-semibold tracking-tight md:text-5xl">
            Everything the reply needs.
          </h2>
          <p className="mx-auto mt-4 max-w-xl text-base leading-7 text-muted">
            The whole outbound machine: discovery, research, deal help, pipeline, inbox, and
            follow-ups in one place, each showing its work.
          </p>
        </Reveal>
        <div className="mx-auto max-w-6xl">
          <FeatureTabs />
        </div>
      </section>

      {/* ── Audiences ────────────────────────────────────────────────── */}
      <section className="px-6 py-16 lg:px-12">
        <Reveal className="mx-auto mb-10 max-w-2xl text-center">
          <h2 className="font-display text-3xl font-semibold tracking-tight md:text-5xl">
            Replaces the whole SDR,<br />not just the <span className="grad-text">writing</span>.
          </h2>
        </Reveal>
        <div className="mx-auto grid max-w-6xl gap-5 md:grid-cols-3">
          {AUDIENCES.map((a, i) => (
            <Reveal key={a.title} delay={i * 0.07}>
              <div className="group glass hover-lift h-full rounded-2xl border border-border p-7 shadow-card">
                <span className="grid size-12 place-items-center rounded-full bg-accent-soft text-accent transition-all duration-300 ease-smooth group-hover:scale-110 group-hover:bg-accent group-hover:text-accent-ink">
                  <a.icon className="size-5" />
                </span>
                <h3 className="mt-6 text-xl font-semibold text-text">{a.title}</h3>
                <p className="mt-3 text-sm leading-6 text-text-2">{a.body}</p>
              </div>
            </Reveal>
          ))}
        </div>
      </section>

      {/* ── Differentiators ──────────────────────────────────────────── */}
      <section className="px-6 py-16 lg:px-12">
        <Reveal className="mx-auto mb-10 max-w-3xl text-center">
          <h2 className="font-display text-3xl font-semibold tracking-tight md:text-5xl">
            Most tools were built for volume.<br />
            <span className="grad-text">Saqua was built for the reply.</span>
          </h2>
        </Reveal>
        <div className="mx-auto grid max-w-6xl gap-5 md:grid-cols-3">
          {DIFFERENTIATORS.map((d, i) => (
            <Reveal key={d.title} delay={i * 0.07}>
              <div className="group glass hover-lift h-full rounded-2xl border border-border p-7 shadow-card">
                <span className="grid size-12 place-items-center rounded-full bg-accent-soft text-accent transition-all duration-300 ease-smooth group-hover:scale-110 group-hover:bg-accent group-hover:text-accent-ink">
                  <d.icon className="size-5" />
                </span>
                <h3 className="mt-6 text-xl font-semibold text-text">{d.title}</h3>
                <p className="mt-3 text-sm leading-6 text-text-2">{d.body}</p>
              </div>
            </Reveal>
          ))}
        </div>
      </section>

      {/* ── Principle quote ──────────────────────────────────────────── */}
      <section className="px-6 py-16 text-center lg:px-12">
        <Reveal className="mx-auto max-w-3xl">
          <h2 className="font-display text-3xl font-medium leading-tight tracking-tight md:text-5xl">
            &ldquo;If a detail isn&apos;t on the page,<br />it never shows up in the{" "}
            <span className="grad-text">email</span>.&rdquo;
          </h2>
          <p className="mt-5 text-muted">The one rule Saqua won&apos;t break.</p>
        </Reveal>
      </section>

      {/* ── Gradient CTA finale ──────────────────────────────────────── */}
      {/* id="waitlist" is the single destination every pre-launch CTA scrolls to
          (nav, pricing cards, /sign-up redirect). */}
      <section id="waitlist" className="scroll-mt-24 px-6 py-16 lg:px-12">
        <Reveal className="mx-auto max-w-6xl">
          <div className="group cta-gradient relative overflow-hidden rounded-[28px] px-6 py-16 text-center text-white shadow-pop transition-shadow duration-300 hover:shadow-[0_36px_90px_-24px_rgba(79,90,247,0.55)] md:py-20">
            <span className="shine" aria-hidden />
            {/* The mark, blown up past the card's own bounds and barely there —
                canvas-on-gradient so it reads as a watermark, not a logo drop. The
                card clips it, which is what gives the cropped-swell look. */}
            <svg
              aria-hidden
              viewBox="0 0 96 56"
              fill="rgba(246,244,242,0.11)"
              className="pointer-events-none absolute -left-24 top-1/2 w-[1100px] max-w-none -translate-y-1/2"
            >
              <path d="M9 40C24 31 38 30 52 20C62 13 75 13 86 21C76 17 64 19 53 26C40 35 26 38 12 44Z" />
              <path d="M56 22C71 12 89 20 85 37C82 50 66 53 57 45C67 49 78 44 78 34C78 25 66 26 58 31Z" />
            </svg>
            <div className="relative">
              <h2 className="mx-auto max-w-2xl font-display text-3xl font-semibold tracking-tight text-white md:text-5xl">
                Your next customer is out there. Go find them.
              </h2>
              <p className="mx-auto mt-4 max-w-xl text-base leading-7 text-white/85">
                Join founders in 30 countries building predictable revenue with Saqua.
              </p>
              <HeroForm className="mt-8" tone="onGradient" label="Join the waitlist" source="cta" />
            </div>
          </div>
        </Reveal>
      </section>

      <SiteFooter />
    </main>
  );
}
