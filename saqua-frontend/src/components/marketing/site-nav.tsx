"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AnimatedLogo } from "@/components/ui/animated-logo";
import { PRELAUNCH, WAITLIST_ANCHOR } from "@/lib/launch";
import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/#pipeline", label: "How it works" },
  { href: "/pricing", label: "Pricing" },
  { href: "/about", label: "About" },
];

/**
 * Floating pill nav (gosollo language): flush and transparent over the hero glow
 * at the top, then detaches into a centered, frosted white capsule with a soft
 * shadow once scrolled. Shared across every marketing page.
 */
export function SiteNav() {
  const [stuck, setStuck] = useState(false);
  useEffect(() => {
    const onScroll = () => setStuck(window.scrollY > 16);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <div className="fixed inset-x-0 top-0 z-50 px-4 pt-4">
      <div
        className={cn(
          "mx-auto flex h-14 items-center justify-between gap-5 rounded-2xl border border-transparent px-4 transition-all duration-300 ease-smooth",
          stuck
            ? "max-w-4xl border-border bg-white/85 px-3 shadow-nav backdrop-blur-md backdrop-saturate-150"
            : "max-w-6xl",
        )}
      >
        <Link href="/" className="flex items-center gap-2 font-display text-lg font-semibold tracking-tight">
          <AnimatedLogo markClassName="h-6 w-auto" />
          Saqua
        </Link>
        <nav className="hidden items-center gap-1 md:flex">
          {LINKS.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className="rounded-xs px-3 py-2 text-sm font-medium text-text/85 transition-colors hover:bg-black/[0.04] hover:text-text"
            >
              {l.label}
            </Link>
          ))}
        </nav>
        <div className="flex items-center gap-2">
          {/* Pre-launch: no login path. The only action is joining the waitlist. */}
          <Button asChild variant="primary" size="sm" className="rounded-full">
            <Link href={PRELAUNCH ? WAITLIST_ANCHOR : "/sign-up"}>
              {PRELAUNCH ? "Join the waitlist" : "Get started"} <ArrowUpRight className="size-4" />
            </Link>
          </Button>
        </div>
      </div>
    </div>
  );
}
