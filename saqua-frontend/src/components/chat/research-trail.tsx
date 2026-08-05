"use client";

import { useMemo, useState } from "react";
import {
  Check,
  ChevronDown,
  ExternalLink,
  Loader2,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import type { ResearchTrailEvent, TrailSource } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * The Research Trail — one compact, reusable view of what Saqua ACTUALLY did,
 * built from real backend tool events (chat/research_trail.py). It is deliberately
 * NOT a fake chat transcript: an animated active step, completed checkmarks, and a
 * collapsible detail list with clickable evidence.
 *
 * It renders the SAME data live (events streaming in) and restored (the persisted
 * trail on a reloaded thread), so nothing is ever replayed as if it were happening
 * again. Every source link is scheme-validated here too (defence in depth), opens
 * in a new tab with rel="noopener noreferrer", and is de-duplicated.
 */

// Client-side URL guard (the backend already validated + stored only safe URLs;
// this is defence in depth so a hand-crafted payload can never inject a scheme).
function safeHref(url: string | null | undefined): string | null {
  if (!url || typeof url !== "string") return null;
  const u = url.trim();
  if (/\s/.test(u)) return null;
  return /^https?:\/\/[^/]+\.[^/]+/i.test(u) ? u : null;
}

function EvidenceLink({ source }: { source: TrailSource }) {
  const href = safeHref(source.url);
  if (!href) return null;
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(
        "inline-flex max-w-full items-center gap-1 rounded-md border px-2 py-0.5 text-xs transition-colors",
        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        source.official
          ? "border-accent-line bg-accent-soft/50 text-accent hover:bg-accent-soft"
          : "border-border bg-black/[0.02] text-text-2 hover:text-text",
      )}
      title={`${source.official ? "Official source" : "Source"}: ${source.url}`}
    >
      <span className="truncate">{source.title || source.domain || href}</span>
      {source.domain && source.title !== source.domain && (
        <span className="shrink-0 text-faint">· {source.domain}</span>
      )}
      <ExternalLink className="size-3 shrink-0 opacity-60" />
    </a>
  );
}

type Row = {
  key: string;
  label: string;
  target?: string | null;
  status: ResearchTrailEvent["status"];
  detail?: string | null;
  sources: TrailSource[];
};

// Collapse the raw events into one row per (run, step): a running event followed
// by its completion becomes a single row that ends in the terminal status, and
// evidence from the completion is what we show. De-duplicated by event_id upstream.
function toRows(events: ResearchTrailEvent[]): Row[] {
  const order: string[] = [];
  const byKey = new Map<string, Row>();
  const seenUrls = new Map<string, Set<string>>();
  for (const e of events) {
    const key = `${e.run_id}:${e.event_type}`;
    if (!byKey.has(key)) {
      order.push(key);
      byKey.set(key, { key, label: e.label, target: e.target, status: e.status, detail: e.detail, sources: [] });
      seenUrls.set(key, new Set());
    }
    const row = byKey.get(key)!;
    // Terminal statuses win; a later running never overrides a completed/failed.
    if (e.status === "completed" || e.status === "failed") {
      row.status = e.status;
      row.label = e.label;
      row.detail = e.detail ?? row.detail;
    } else if (row.status !== "completed" && row.status !== "failed") {
      row.status = e.status;
    }
    const urls = seenUrls.get(key)!;
    for (const s of e.sources || []) {
      if (!s?.url || urls.has(s.url)) continue;
      urls.add(s.url);
      row.sources.push(s);
    }
  }
  return order.map((k) => byKey.get(k)!);
}

export function ResearchTrail({
  events,
  live = false,
  defaultOpen = false,
}: {
  events: ResearchTrailEvent[];
  /** True while a turn is streaming — the header shows the active step. */
  live?: boolean;
  defaultOpen?: boolean;
}) {
  const rows = useMemo(() => toRows(events), [events]);
  const [open, setOpen] = useState(defaultOpen);

  if (rows.length === 0) return null;

  const active = rows.find((r) => r.status === "running" || r.status === "pending");
  const done = rows.filter((r) => r.status === "completed").length;
  const failed = rows.some((r) => r.status === "failed");
  const hasEvidence = rows.some((r) => r.sources.length > 0);

  return (
    <div className="rounded-xl border border-border bg-card/60">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        {active && live ? (
          <Loader2 className="size-4 shrink-0 animate-spin text-accent" />
        ) : failed ? (
          <ShieldAlert className="size-4 shrink-0 text-[color:var(--danger,#b42318)]" />
        ) : (
          <Sparkles className="size-4 shrink-0 text-accent" />
        )}
        <span className="min-w-0 flex-1 truncate font-medium text-text">
          {active && live ? active.label : "Research trail"}
          {active?.target && live ? <span className="text-text-2"> · {active.target}</span> : null}
        </span>
        <span className="shrink-0 text-xs text-muted">
          {done} step{done === 1 ? "" : "s"}
          {hasEvidence ? " · evidence" : ""}
        </span>
        <ChevronDown
          className={cn("size-4 shrink-0 text-muted transition-transform", open && "rotate-180")}
        />
      </button>

      {open && (
        <ul className="space-y-2.5 border-t border-border-faint px-3.5 py-3">
          {rows.map((r) => (
            <li key={r.key} className="flex gap-2.5 text-sm">
              <span className="mt-0.5 shrink-0">
                {r.status === "completed" ? (
                  <Check className="size-4 text-[color:var(--success,#0a7d43)]" />
                ) : r.status === "failed" ? (
                  <ShieldAlert className="size-4 text-[color:var(--danger,#b42318)]" />
                ) : (
                  <Loader2 className={cn("size-4 text-accent", live && "animate-spin")} />
                )}
              </span>
              <div className="min-w-0 flex-1">
                <div className="text-text">
                  {r.label}
                  {r.target && <span className="text-text-2"> · {r.target}</span>}
                </div>
                {r.status === "failed" && r.detail && (
                  <div className="mt-0.5 text-xs text-text-2">{r.detail}</div>
                )}
                {r.sources.length > 0 && (
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {r.sources.map((s) => (
                      <EvidenceLink key={s.url} source={s} />
                    ))}
                  </div>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
