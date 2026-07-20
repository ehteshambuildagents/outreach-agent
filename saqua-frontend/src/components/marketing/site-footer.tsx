import Link from "next/link";
import { Mail } from "lucide-react";
import { Logo } from "@/components/ui/logo";
import { PRELAUNCH, WAITLIST_ANCHOR } from "@/lib/launch";

const COLS: { title: string; links: { href: string; label: string }[] }[] = [
  {
    title: "Product",
    links: [
      { href: "/#pipeline", label: "How it works" },
      { href: "/pricing", label: "Pricing" },
      { href: "https://cal.com/saqua/demo-call", label: "Book a demo" },
    ],
  },
  {
    title: "Company",
    links: [
      { href: "/about", label: "About" },
      { href: "/contact", label: "Contact" },
    ],
  },
  {
    title: "Legal",
    links: [
      { href: "/privacy", label: "Privacy" },
      { href: "/terms", label: "Terms" },
    ],
  },
  {
    title: "Get started",
    links: [
      // Pre-launch: no login path — the waitlist is the only entry point.
      PRELAUNCH
        ? { href: WAITLIST_ANCHOR, label: "Join the waitlist" }
        : { href: "/sign-up", label: "Get started" },
    ],
  },
];

/** Footer that sits on the same warm glow as the hero — a gosollo-style bookend. */
export function SiteFooter() {
  return (
    <footer className="relative isolate overflow-hidden px-6 pb-10 pt-24 lg:px-12">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 bottom-[-20%] -z-10 h-[70%]"
        style={{
          background:
            "radial-gradient(60% 100% at 50% 120%, rgba(245,163,5,.36), rgba(252,214,189,.26) 45%, transparent 72%)",
        }}
      />
      <div className="mx-auto max-w-6xl">
        <div className="grid gap-10 sm:grid-cols-2 lg:grid-cols-[1.5fr_repeat(4,1fr)]">
          <div className="max-w-[26ch]">
            <div className="flex items-center gap-2 font-display text-3xl font-semibold tracking-tight">
              <Logo className="h-7 w-auto" />
              Saqua
            </div>
            <p className="mt-3 text-sm text-muted">
              Researched outbound that reads like a founder wrote it, not a template.
            </p>
          </div>
          {COLS.map((col) => (
            <div key={col.title}>
              <h4 className="mb-4 text-2xs font-semibold uppercase tracking-[0.1em] text-muted">
                {col.title}
              </h4>
              {col.links.map((l) => (
                <Link
                  key={l.label}
                  href={l.href}
                  className="block py-1 text-sm text-text-2 transition-colors hover:text-accent"
                >
                  {l.label}
                </Link>
              ))}
            </div>
          ))}
        </div>
        <div className="mt-16 flex flex-col items-center justify-between gap-4 border-t border-border pt-6 text-sm text-muted sm:flex-row">
          <span>© {new Date().getFullYear()} Saqua. All rights reserved.</span>
          <a
            href="mailto:support@saqua.io"
            aria-label="Email support"
            className="grid size-9 place-items-center rounded-sm bg-text text-bg transition-opacity hover:opacity-90"
          >
            <Mail className="size-4" />
          </a>
        </div>
      </div>
    </footer>
  );
}
