import {
  Zap,
  BadgePercent,
  Rocket,
  Users,
  Briefcase,
  Sparkles,
  Waves,
  ShieldCheck,
  Check,
  X,
} from "lucide-react";
import { Reveal } from "@/components/ui/reveal";
import { SiteNav } from "@/components/marketing/site-nav";
import { SiteFooter } from "@/components/marketing/site-footer";
import { HeroForm } from "@/components/marketing/hero-form";
import { FeatureTabs } from "@/components/marketing/feature-tabs";
import { PipelineStory } from "@/components/marketing/pipeline-story";

const AUDIENCES = [
  {
    icon: Rocket,
    title: "Founders selling their own product",
    body: "Nobody pitches it like you do. Saqua does the research and the first draft, so every intro still sounds like you wrote it without you writing each one.",
  },
  {
    icon: Users,
    title: "Small teams without an SDR",
    body: "Drop the sequence tool, the enrichment tab, and the shared template doc. Research, drafts, follow-ups, and replies all sit where the whole team can see them.",
  },
  {
    icon: Briefcase,
    title: "Agencies running client outbound",
    body: "Personal outbound for every client without cloning yourself. Saqua keeps each account's voice, offer, and target customer straight.",
  },
];

// The before/after worksheet. Kept to four beats a side so the two columns stay
// the same height without hand-tuning.
const PAIN = [
  "Hours spent deciding which companies are even worth contacting",
  "Templates that read automated and get quietly ignored",
  "Deals lost to follow-ups you meant to send and never did",
  "Good months after quiet ones, and no idea what made the difference",
];

const GAIN = [
  "Companies worth contacting, scored and waiting each morning",
  "Every opener built on a real detail, with the quote behind it",
  "Follow-ups that go out on real days and stop when someone replies",
  "One hour a week that actually moves the pipeline",
];

const DIFFERENTIATORS = [
  {
    icon: Sparkles,
    title: "It only says what it can prove",
    body: "Every claim carries the exact quote it came from. Generic AI fills the gap with something invented, and one wrong detail costs you the reply. Saqua leaves it out instead.",
  },
  {
    icon: Waves,
    title: "It sounds like you, not a sequence",
    body: "Short, specific, human. Written the way a founder writes when they only have one shot, and rewritten by Saqua itself if a draft reads automated.",
  },
  {
    icon: ShieldCheck,
    title: "Nothing sends unchecked",
    body: "Every draft clears a deliverability and spend check first. If something looks risky, Saqua blocks it and tells you why instead of quietly sending it anyway.",
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
            <Zap className="size-3.5 text-accent" /> Outbound that shows its work
          </span>
        </Reveal>
        <Reveal delay={0.05}>
          {/* 20ch, not 18: at md the first line measures ~1005px, so the old cap
              broke it mid-phrase and stranded the last word on its own line. */}
          <h1 className="mx-auto mt-7 max-w-[20ch] font-display text-5xl font-medium leading-[1.03] tracking-[-0.03em] md:text-7xl">
            Find, research, write, send.<br />Without the <span className="grad-text-anim">headcount</span>.
          </h1>
        </Reveal>
        <Reveal delay={0.1}>
          <p className="mx-auto mt-6 max-w-2xl text-lg leading-8 text-muted">
            For founders doing their own sales. Saqua finds companies worth contacting, reads what
            each one has actually published, and writes the first email around a real detail it
            found. You approve every send, and follow-ups stop the moment someone replies.
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
            It runs the real pipeline on a real company you choose. No account, just a Gmail
            address.{" "}
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
            Founding pricing, locked for life
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
              Yours if you join the waitlist before launch day. Early access stays limited
              while Google reviews our Gmail access.
            </p>
          </div>
        </Reveal>

        {/* Honest pre-launch proof: waitlist reach, not customers. No stock faces. */}
        <Reveal delay={0.25}>
          <p className="mt-8 text-sm text-muted">
            Founders from <span className="font-medium text-text">30 countries</span> have joined
            the waitlist
          </p>
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
            A lead list in one tab, an enrichment tool in another, a template doc, your inbox.
            Nothing talks to anything, so outbound only happens on the afternoons you have
            spare.
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

      {/* ── How it works ─────────────────────────────────────────────── */}
      {/* id="pipeline" is what the nav and footer "How it works" links point at.
          Until this section existed, both of those links went nowhere. */}
      <section id="pipeline" className="scroll-mt-24 px-6 py-16 lg:px-12">
        <Reveal className="mx-auto mb-12 max-w-2xl text-center">
          <span className="text-sm font-semibold text-accent">How it works</span>
          <h2 className="mt-3 font-display text-3xl font-semibold tracking-tight md:text-5xl">
            Four steps, and you only do one of them.
          </h2>
          <p className="mx-auto mt-4 max-w-xl text-base leading-7 text-muted">
            From one sentence about your customer to a reply in your inbox. The only step that
            needs you is the yes.
          </p>
        </Reveal>
        <PipelineStory />
        {/* The demo offer lands here, where a reader has just understood the
            pipeline and the obvious next thought is "show me". */}
        <Reveal className="mt-14 text-center">
          <p className="text-sm text-muted">
            Rather see it than read it?{" "}
            <a href="/demo" className="font-medium text-accent hover:underline">
              Run these four steps on a company you pick
            </a>
            .
          </p>
        </Reveal>
      </section>

      {/* ── Feature dashboard (tab switcher) ─────────────────────────── */}
      <section className="px-6 py-16 lg:px-12">
        <Reveal className="mx-auto mb-10 max-w-2xl text-center">
          <span className="text-sm font-semibold text-accent">One workspace</span>
          <h2 className="mt-3 font-display text-3xl font-semibold tracking-tight md:text-5xl">
            One tab instead of five.
          </h2>
          <p className="mx-auto mt-4 max-w-xl text-base leading-7 text-muted">
            Discovery, research, deal help, pipeline, inbox, and follow-ups in a single
            workspace. Every one of them shows the evidence behind what it did.
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
            It does the whole job,<br />not just the <span className="grad-text">wording</span>.
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
                Get in before launch day.
              </h2>
              <p className="mx-auto mt-4 max-w-xl text-base leading-7 text-white/85">
                Founding pricing is 40% off for life, and it only applies to the waitlist. One
                email when Saqua opens. Nothing else.
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
