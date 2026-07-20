import Link from "next/link";
import { SiteNav } from "@/components/marketing/site-nav";
import { SiteFooter } from "@/components/marketing/site-footer";

export const metadata = {
  title: "Privacy Policy — Saqua",
  description: "How Saqua collects, uses, and protects your data.",
};

// Concise in-app policy. Mirrors the canonical full policy served by the
// marketing site (/privacy.html); keep the two in sync when either changes.
export default function PrivacyPage() {
  return (
    // No bg here: it would paint over the -z-10 glow (see globals.css .page-light).
    <main className="relative min-h-screen overflow-clip text-text">
      <div aria-hidden className="hero-glow-cool pointer-events-none absolute inset-x-0 top-0 -z-10 h-[420px]" />

      <SiteNav />

      <div className="mx-auto max-w-3xl px-6 pb-24 pt-36">
        <p className="text-sm font-semibold text-accent">Legal</p>
        <h1 className="mt-2 font-display text-4xl font-medium tracking-[-0.03em] md:text-5xl">Privacy Policy</h1>
        <p className="mt-2 text-sm text-muted">Last updated: 13 July 2026.</p>

        <div className="glass mt-6 rounded-lg border border-border p-4 text-sm text-text-2 shadow-card">
          A clear, plain-English policy written in good faith — not yet lawyer-reviewed.
          We intend to have it formally reviewed before general availability.
        </div>

        <section className="mt-8 space-y-6 text-sm leading-relaxed text-text">
          <div>
            <h2 className="text-lg font-semibold">What we collect</h2>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-text-2">
              <li>Account info (name, email) from our auth provider, Clerk.</li>
              <li>Email content you draft, edit, and send through Saqua.</li>
              <li>Prospect and research data — the companies you research and the public info gathered about them.</li>
              <li>Encrypted mailbox OAuth tokens (Gmail/Outlook), used to send and detect replies. Never shown in the browser.</li>
              <li>Your conversations and workspace drafts.</li>
              <li>Operational metadata: usage, cost/telemetry, and error logs used to run the service.</li>
              <li>
                Waitlist email address, if you join before launch. We store it only to email you
                when Saqua opens, we confirm it first so nobody is added without asking, and every
                message carries a one-click unsubscribe. It is never sold or used for anything else,
                and unsubscribing removes you from all waitlist mail.
              </li>
            </ul>
          </div>

          <div>
            <h2 className="text-lg font-semibold">How we use it</h2>
            <p className="mt-2 text-text-2">
              To research companies, draft personalized outreach, send mail and detect replies
              only when you connect a mailbox and explicitly ask, keep your workspace available,
              and operate/secure the service. We don&apos;t sell your data or use your email
              content for advertising. Saqua&apos;s use of Google user data follows the{" "}
              <a
                className="text-accent underline"
                href="https://developers.google.com/terms/api-services-user-data-policy"
                target="_blank"
                rel="noopener noreferrer"
              >
                Google API Services User Data Policy
              </a>
              , including its Limited Use requirements.
            </p>
          </div>

          <div>
            <h2 className="text-lg font-semibold">Third parties your data passes through</h2>
            <p className="mt-2 text-text-2">
              Anthropic (Claude), Firecrawl, Tavily, Exa, Jina, Hunter, the X (Twitter) API,
              Google (Gmail), Microsoft (Outlook), and Clerk — each processes only the minimum
              needed to return your result.
            </p>
          </div>

          <div>
            <h2 className="text-lg font-semibold">Retention &amp; your choices</h2>
            <p className="mt-2 text-text-2">
              We keep your data while your account is active. You can delete any conversation,
              disconnect a mailbox to revoke tokens, or contact us to access or delete your
              account data.
            </p>
          </div>

          <div>
            <h2 className="text-lg font-semibold">Contact</h2>
            <p className="mt-2 text-text-2">
              Email{" "}
              <a className="text-accent underline" href="mailto:support@saqua.io">
                support@saqua.io
              </a>
              .
            </p>
          </div>
        </section>

        <p className="mt-10 text-sm text-muted">
          <Link className="text-accent underline" href="/terms">
            Terms of Service
          </Link>
        </p>
      </div>

      <SiteFooter />
    </main>
  );
}
