"use client";

import Link from "next/link";
import { Sparkles, MessageSquare, X } from "lucide-react";
import { useDemo } from "@/components/demo/demo-provider";

/**
 * Persistent strip shown across the real app while a demo session is live. Makes
 * the sandbox honest and unmissable: real pipeline, drafts only, time-boxed, with
 * the waitlist one click away. Renders nothing for real members.
 */
export function DemoBanner() {
  const { isDemo, turnsLeft, endSession } = useDemo();
  if (!isDemo) return null;

  // A single, compact status bar: what this is, how much is left, and the two
  // actions (join / exit). Duration is deliberately not shown, a countdown just
  // rushes the visitor. The waitlist CTA lives ONLY here, not also in the top bar.
  return (
    <div className="border-b border-accent-line bg-accent-soft/60">
      <div className="mx-auto flex max-w-[1240px] flex-wrap items-center gap-x-3 gap-y-1 px-5 py-2 text-xs md:px-8">
        <span className="inline-flex items-center gap-1.5 font-semibold text-accent">
          <Sparkles className="size-3.5" /> Live demo
        </span>
        <span className="text-text-2">
          Real research and drafts. Sending stays off until Gmail clears Google&apos;s review.
        </span>
        {turnsLeft !== null && (
          <span className="inline-flex items-center gap-1 font-medium text-muted">
            <MessageSquare className="size-3.5" />
            {turnsLeft} message{turnsLeft === 1 ? "" : "s"} remaining
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
            aria-label="Exit demo session"
            className="inline-flex items-center gap-1 text-muted transition-colors hover:text-text"
          >
            <X className="size-3.5" /> Exit demo
          </button>
        </span>
      </div>
    </div>
  );
}
