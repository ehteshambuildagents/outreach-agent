import Link from "next/link";
import { SiteNav } from "@/components/marketing/site-nav";
import { SiteFooter } from "@/components/marketing/site-footer";

export const metadata = {
  title: "Privacy Policy — Saqua",
  description: "How Saqua collects, uses, and protects your data.",
};

// Full policy. Kept in sync with the legacy static mirror at web/privacy.html
// (which redirects here via next.config). This page is the canonical policy URL
// given to Google for OAuth verification: https://www.saqua.io/privacy
export default function PrivacyPage() {
  return (
    // No bg here: it would paint over the -z-10 glow (see globals.css .page-light).
    <main className="relative min-h-screen overflow-clip text-text">
      <div aria-hidden className="hero-glow-cool pointer-events-none absolute inset-x-0 top-0 -z-10 h-[420px]" />

      <SiteNav />

      <div className="mx-auto max-w-3xl px-6 pb-24 pt-36">
        <p className="text-sm font-semibold text-accent">Legal</p>
        <h1 className="mt-2 font-display text-4xl font-medium tracking-[-0.03em] md:text-5xl">Privacy Policy</h1>
        <p className="mt-2 text-sm text-muted">Last updated: 21 July 2026.</p>

        <div className="glass mt-6 rounded-lg border border-border p-4 text-sm text-text-2 shadow-card">
          A clear, plain-English policy written in good faith. We intend to have it formally
          reviewed before general availability.
        </div>

        <section className="mt-8 space-y-6 text-sm leading-relaxed text-text">
          <div>
            <h2 className="text-lg font-semibold">What we collect</h2>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-text-2">
              <li>Account info (name, email) from our auth provider, Clerk.</li>
              <li>Email content you draft, edit, and send through Saqua.</li>
              <li>Prospect and research data — the companies you research and the public info gathered about them.</li>
              <li>
                Encrypted mailbox OAuth tokens (Gmail/Outlook), used to send email and detect
                replies. For Gmail we read only message metadata (identifiers and labels) — never
                message content. Tokens are never shown in the browser.
              </li>
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
            <h2 className="text-lg font-semibold">Google user data (Gmail)</h2>
            <p className="mt-2 text-text-2">
              When you connect a Google account, Saqua requests only two Gmail permissions:
            </p>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-text-2">
              <li>
                <span className="font-medium text-text">Send</span> (<code>gmail.send</code>) — to
                send the emails you draft and approve, and their follow-ups.
              </li>
              <li>
                <span className="font-medium text-text">Metadata</span> (<code>gmail.metadata</code>)
                — to detect replies to your sequences. This scope grants access only to message and
                thread <span className="font-medium text-text">identifiers and labels</span> (such as
                INBOX and SENT) — never the subject or body of any message. Saqua cannot and does not
                read your email content. When a reply is detected, Saqua stores only the message&apos;s
                opaque ID and a timestamp, and uses them solely to stop the follow-up sequence.
              </li>
            </ul>
            <p className="mt-3 text-text-2">
              Saqua&apos;s use of information received from Google APIs adheres to the{" "}
              <a
                className="text-accent underline"
                href="https://developers.google.com/terms/api-services-user-data-policy"
                target="_blank"
                rel="noopener noreferrer"
              >
                Google API Services User Data Policy
              </a>
              , including its Limited Use requirements. Specifically:
            </p>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-text-2">
              <li>
                We do <span className="font-medium text-text">not</span> use Google user data to
                develop, improve, or train generalized or non-personalized AI or ML models.
              </li>
              <li>We do not sell or transfer Google user data, and we do not use it for advertising.</li>
              <li>
                We do not allow humans to read your Google user data, except with your explicit
                consent, as needed for security or to comply with the law, or on data that has been
                aggregated and anonymized.
              </li>
            </ul>
            <p className="mt-3 text-text-2">
              You can revoke Saqua&apos;s access at any time from your{" "}
              <a
                className="text-accent underline"
                href="https://myaccount.google.com/permissions"
                target="_blank"
                rel="noopener noreferrer"
              >
                Google Account permissions
              </a>{" "}
              or by disconnecting the mailbox in the app; disconnecting deletes the stored tokens.
            </p>
          </div>

          <div>
            <h2 className="text-lg font-semibold">How we use it</h2>
            <p className="mt-2 text-text-2">
              To research companies, draft personalized outreach, send mail and detect replies only
              when you connect a mailbox and explicitly ask, keep your workspace available, and
              operate/secure the service. We don&apos;t sell your data or use your email content for
              advertising.
            </p>
          </div>

          <div>
            <h2 className="text-lg font-semibold">Third parties your data passes through</h2>
            <p className="mt-2 text-text-2">
              Anthropic (Claude), Firecrawl, Tavily, Exa, Jina, Hunter, the X (Twitter) API, Google
              (Gmail), Microsoft (Outlook), and Clerk — each processes only the minimum needed to
              return your result. Your Gmail data is used only within Saqua for reply detection — it
              is not sent to Anthropic or any AI provider.
            </p>
          </div>

          <div>
            <h2 className="text-lg font-semibold">Security</h2>
            <p className="mt-2 text-text-2">
              Mailbox OAuth tokens and other secrets are encrypted at rest; our database stores only
              ciphertext, never raw tokens. Traffic to and from Saqua uses TLS. API keys and secrets
              live only in our server environment — never shown in the interface or sent to your
              browser. Access to production data is limited to what is needed to operate the service.
            </p>
          </div>

          <div>
            <h2 className="text-lg font-semibold">Retention &amp; deletion</h2>
            <p className="mt-2 text-text-2">
              We keep your conversations, drafts, and research while your account is active. You can
              delete any conversation at any time, which removes it from our systems. For Gmail, we
              store no message content at any point — only the opaque message IDs and timestamps used
              to detect replies. Mailbox tokens are kept until you disconnect the mailbox or delete
              your account, at which point they are deleted and access is revoked. Operational logs
              (usage, errors) are kept for a limited period for security and reliability, then aged
              out. If you delete your account, we remove your workspace data and revoke stored
              tokens; minimal records may be retained where required by law. To access or delete your
              data, email{" "}
              <a className="text-accent underline" href="mailto:support@saqua.io">
                support@saqua.io
              </a>
              .
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
