"use client";

import { useState } from "react";
import { X, Copy, Check, Mail, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { EmailCardData } from "@/lib/api";

/**
 * The draft canvas — opens beside the conversation (artifact pattern), never
 * inline and never a separate page. Shows the fit score + pre-send checks (only
 * when the payload carries them), subject, and the generated email body. This is
 * the one place the serif treatment appears in the app: real generated copy.
 */
export function ArtifactPanel({ draft, onClose }: { draft: EmailCardData; onClose: () => void }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(`Subject: ${draft.subject || ""}\n\n${draft.body || ""}`);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard may be blocked (no HTTPS / permissions) */
    }
  }

  return (
    <div className="glass-panel flex h-full flex-col border-l border-border">
      <div className="flex items-center gap-2 border-b border-border-faint px-4 py-3">
        <Mail className="size-4 text-accent" />
        <span className="text-sm font-medium text-text">{draft.label || "Draft"}</span>
        <button
          onClick={onClose}
          aria-label="Close draft"
          className="ml-auto grid size-7 place-items-center rounded-md text-muted hover:bg-black/[0.05] hover:text-text"
        >
          <X className="size-4" />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
        {/* Metadata strip — mono for the data-like values, shown only when present */}
        <div className="mb-4 flex flex-wrap items-center gap-2 text-xs">
          {typeof draft.fit_score === "number" && (
            <span className="inline-flex items-center gap-1.5 rounded-md border border-accent-line bg-accent-soft px-2 py-1 text-accent">
              <span className="font-mono font-semibold leading-none">{draft.fit_score}</span>
              <span className="text-[10px] uppercase tracking-wide">fit</span>
            </span>
          )}
          {typeof draft.checks_passed === "number" && (
            <span className="inline-flex items-center gap-1.5 rounded-md border border-accent-line bg-accent-soft px-2 py-1 text-accent">
              <ShieldCheck className="size-3" />
              <span className="font-mono">{draft.checks_passed}</span> checks
            </span>
          )}
          {(draft.to || draft.company) && (
            <span className="font-mono text-muted">to: {draft.to || draft.company}</span>
          )}
        </div>

        {draft.subject && (
          <div className="mb-3">
            <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted">Subject</div>
            <div className="text-sm font-medium text-text">{draft.subject}</div>
          </div>
        )}

        <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted">Body</div>
        {/* The single serif treatment across the product: Saqua's generated copy. */}
        <p className="whitespace-pre-wrap font-serif text-[15px] leading-7 text-text">{draft.body}</p>
      </div>

      <div className="flex items-center gap-2 border-t border-border-faint px-4 py-3">
        <Button size="sm" variant="secondary" onClick={copy}>
          {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
          {copied ? "Copied" : "Copy draft"}
        </Button>
        <span className="text-[11px] text-muted">Nothing sends from here. You copy and send it yourself.</span>
      </div>
    </div>
  );
}
