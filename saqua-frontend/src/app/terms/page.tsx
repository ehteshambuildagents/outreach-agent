import Link from "next/link";
import { Logo } from "@/components/ui/logo";
import { Button } from "@/components/ui/button";

export const metadata = {
  title: "Terms of Service — Saqua",
  description: "The terms for using Saqua while it's in early access.",
};

// Concise in-app terms. Mirrors the canonical full version served by the
// marketing site (/terms.html); keep the two in sync when either changes.
export default function TermsPage() {
  return (
    <main className="relative min-h-screen overflow-hidden bg-bg text-text">
      <div aria-hidden className="pointer-events-none absolute inset-0 -z-10">
        <div className="bloom-indigo animate-drift absolute -left-40 -top-40 size-[560px] rounded-full" />
        <div className="bloom-teal animate-drift-2 absolute -right-48 top-[40%] size-[520px] rounded-full opacity-70" />
      </div>

      <header className="mx-auto flex h-16 max-w-3xl items-center justify-between px-6">
        <Link href="/" className="flex items-center gap-2 text-sm font-semibold">
          <Logo className="h-6 w-auto" /> Saqua
        </Link>
        <Button asChild variant="ghost" size="sm">
          <Link href="/">Back to home</Link>
        </Button>
      </header>

      <div className="mx-auto max-w-3xl px-6 pb-24 pt-8">
        <p className="text-sm text-muted">Legal</p>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight">Terms of Service</h1>
        <p className="mt-2 text-sm text-muted">Last updated: 13 July 2026.</p>

        <div className="glass mt-6 rounded-lg border border-border p-4 text-sm text-text-2">
          A clear, plain-English draft written in good faith — not yet a lawyer-reviewed
          contract. We intend to have it formally reviewed before general availability.
        </div>

        <section className="mt-8 space-y-6 text-sm leading-relaxed text-text-2">
          <div>
            <h2 className="text-lg font-medium text-text">1. Acceptable use</h2>
            <p className="mt-2">
              Use Saqua for legitimate business outreach. Don&apos;t harass, deceive,
              impersonate, scrape unlawfully, or send spam. You&apos;re responsible for
              complying with applicable anti-spam laws (CAN-SPAM, GDPR, CASL) and your mailbox
              provider&apos;s terms.
            </p>
          </div>
          <div>
            <h2 className="text-lg font-medium text-text">2. You own each send</h2>
            <p className="mt-2">
              Saqua drafts email and only sends when you connect a mailbox and explicitly
              approve a send. It never sends on its own. Review every message before it goes out.
            </p>
          </div>
          <div>
            <h2 className="text-lg font-medium text-text">3. AI-generated content</h2>
            <p className="mt-2">
              Saqua uses language models, which can make mistakes. Verify research and drafts
              before acting on them.
            </p>
          </div>
          <div>
            <h2 className="text-lg font-medium text-text">4. Accounts, access &amp; limits</h2>
            <p className="mt-2">
              New accounts may start in a pending state during early access. Per-account usage
              caps apply to paid operations; abnormally high usage may pause automated actions
              pending review.
            </p>
          </div>
          <div>
            <h2 className="text-lg font-medium text-text">5. As-is; your content</h2>
            <p className="mt-2">
              Early-access software is provided &ldquo;as is,&rdquo; without warranties. You keep
              ownership of your research, drafts, and emails. See the{" "}
              <Link className="text-accent-hi underline" href="/privacy">
                Privacy Policy
              </Link>{" "}
              for data handling.
            </p>
          </div>
          <div>
            <h2 className="text-lg font-medium text-text">6. Contact</h2>
            <p className="mt-2">
              Questions? Email{" "}
              <a className="text-accent-hi underline" href="mailto:support@saqua.io">
                support@saqua.io
              </a>
              .
            </p>
          </div>
        </section>
      </div>
    </main>
  );
}
