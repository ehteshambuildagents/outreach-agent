"use client";

import { useState } from "react";
import Link from "next/link";
import { Plus, MessageSquare, Megaphone } from "lucide-react";
import { StatusPill } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { ConversationSummary, CampaignDetail } from "@/lib/api";

/**
 * Chat's own left rail: recent research threads and active campaigns, switchable.
 * Threads load into the conversation in place; campaigns link to their detail page.
 */
export function ThreadRail({
  threads,
  activeId,
  campaigns,
  onSelect,
  onNew,
}: {
  threads: ConversationSummary[];
  activeId: string | null;
  campaigns: CampaignDetail[];
  onSelect: (id: string) => void;
  onNew: () => void;
}) {
  const [tab, setTab] = useState<"threads" | "campaigns">("threads");

  return (
    <div className="flex h-full flex-col">
      <div className="p-3">
        <button
          onClick={onNew}
          className="flex w-full items-center gap-2 rounded-md border border-border bg-white/[0.03] px-3 py-2 text-sm text-text transition-colors hover:border-border-strong hover:bg-white/[0.06]"
        >
          <Plus className="size-4 text-accent-hi" /> New chat
        </button>
      </div>

      {/* Switcher */}
      <div className="mx-3 mb-1 grid grid-cols-2 gap-1 rounded-md border border-border-faint bg-white/[0.02] p-1 text-xs">
        {(["threads", "campaigns"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cn(
              "flex items-center justify-center gap-1.5 rounded-[6px] px-2 py-1.5 capitalize transition-colors",
              tab === t ? "bg-white/[0.06] text-text" : "text-muted hover:text-text-2",
            )}
          >
            {t === "threads" ? <MessageSquare className="size-3.5" /> : <Megaphone className="size-3.5" />}
            {t}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
        {tab === "threads" ? (
          threads.length === 0 ? (
            <p className="px-3 py-6 text-center text-xs text-muted">
              No threads yet. Start one to research or score a prospect.
            </p>
          ) : (
            threads.map((t) => (
              <button
                key={t.id}
                onClick={() => onSelect(t.id)}
                className={cn(
                  "mb-0.5 flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-left text-sm transition-colors",
                  t.id === activeId ? "bg-accent-soft text-text" : "text-text-2 hover:bg-white/[0.04] hover:text-text",
                )}
              >
                <MessageSquare
                  className={cn("size-3.5 shrink-0", t.id === activeId ? "text-accent-hi" : "text-muted")}
                />
                <span className="truncate">{t.title || "New chat"}</span>
              </button>
            ))
          )
        ) : campaigns.length === 0 ? (
          <p className="px-3 py-6 text-center text-xs text-muted">No campaigns yet.</p>
        ) : (
          campaigns.map((c) => (
            <Link
              key={c.id}
              href={`/campaigns/${c.id}`}
              className="mb-0.5 flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-left text-sm text-text-2 transition-colors hover:bg-white/[0.04] hover:text-text"
            >
              <span className="min-w-0 flex-1 truncate">{c.name}</span>
              <StatusPill state={c.status} />
            </Link>
          ))
        )}
      </div>
    </div>
  );
}
