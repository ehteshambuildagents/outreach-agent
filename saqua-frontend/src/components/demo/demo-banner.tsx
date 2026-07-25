"use client";

import Link from "next/link";
import { Sparkles, Clock, X } from "lucide-react";
import { useDemo } from "@/components/demo/demo-provider";

/**
 * Persistent strip shown across the real app while a demo session is live. Makes
 * the sandbox honest and unmissable: real pipeline, drafts only, time-boxed, with
 * the waitlist one click away. Renders nothing for real members.
 */
export function DemoBanner() {
  const { isDemo, expiresAt, turnsUsed, turnsLimit, endSession } = useDemo();
  if (!isDemo) return null;

  const minsLeft =
    expiresAt !== null ? Math.max(0, Math.round((expiresAt * 1000 - Date.now()) / 60000)) : null;
  const turnsLeft = turnsLimit > 0 ? Math.max(0, turnsLimit - turnsUsed) : null;

  return (
    <div className="border-b border-accent-line bg-accent-soft/60">
      <div className="mx-auto flex max-w-[1240px] flex-wrap items-center gap-x-3 gap-y-1 px-5 py-2 text-xs md:px-8">
        <span className="inline-flex items-center gap-1.5 font-semibold text-accent">
          <Sparkles className="size-3.5" /> Live demo
        </span>
        <span className="text-text-2">
          You&apos;re exploring the real product on a sandbox, with real research, scoring, and
          guard-checked drafts. Sending stays off until Gmail clears Google&apos;s review.
        </span>
        {minsLeft !== null && (
          <span className="inline-flex items-center gap-1 text-muted">
            <Clock className="size-3.5" />
            {minsLeft > 0 ? `${minsLeft} min left` : "ending…"}
            {turnsLeft !== null ? ` · ${turnsLeft} message${turnsLeft === 1 ? "" : "s"} left` : ""}
          </span>
        )}
        <span className="ml-auto inline-flex items-center gap-3">
          <Link
            href="/#waitlist"
            className="font-semibold text-accent hover:underline"
            onClick={() => void endSession()}
          >
            Join the waitlist →
          </Link>
          <button
            type="button"
            onClick={() => void endSession()}
            aria-label="End demo session"
            className="inline-flex items-center gap-1 text-muted transition-colors hover:text-text"
          >
            <X className="size-3.5" /> End
          </button>
        </span>
      </div>
    </div>
  );
}
