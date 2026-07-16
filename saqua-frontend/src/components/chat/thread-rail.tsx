"use client";

import { Plus, MessageSquare, Trash2, PenSquare } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ConversationSummary } from "@/lib/api";

/**
 * Chat history rail (Claude/ChatGPT style): a New-chat action up top, then the
 * list of recent conversations with Saqua beneath. Selecting one loads it in
 * place; the compose icon and the top button both start a fresh chat.
 */
export function ThreadRail({
  threads,
  activeId,
  onSelect,
  onNew,
  onDelete,
}: {
  threads: ConversationSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
}) {
  return (
    <div className="flex h-full flex-col">
      <div className="p-3">
        <button
          onClick={onNew}
          className="flex w-full items-center gap-2 rounded-md border border-border bg-white/[0.03] px-3 py-2 text-sm font-medium text-text transition-colors hover:border-border-strong hover:bg-white/[0.06]"
        >
          <Plus className="size-4 text-accent-hi" /> New chat
        </button>
      </div>

      {/* Recents header with a ChatGPT-style compose shortcut */}
      <div className="flex items-center justify-between px-4 pb-1 pt-2">
        <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-faint">Recents</span>
        <button
          onClick={onNew}
          aria-label="New chat"
          title="New chat"
          className="grid size-6 place-items-center rounded text-muted transition-colors hover:bg-white/[0.06] hover:text-text"
        >
          <PenSquare className="size-3.5" />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {threads.length === 0 ? (
          <p className="px-3 py-6 text-center text-xs text-muted">
            No chats yet. Start one to research or score a prospect.
          </p>
        ) : (
          threads.map((t) => (
            <div
              key={t.id}
              className={cn(
                "group mb-0.5 flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors",
                t.id === activeId ? "bg-accent-soft text-text" : "text-text-2 hover:bg-white/[0.04] hover:text-text",
              )}
            >
              <button onClick={() => onSelect(t.id)} className="flex min-w-0 flex-1 items-center gap-2.5 text-left">
                <MessageSquare
                  className={cn("size-3.5 shrink-0", t.id === activeId ? "text-accent-hi" : "text-muted")}
                />
                <span className="truncate">{t.title || "New chat"}</span>
              </button>
              <button
                onClick={() => onDelete(t.id)}
                aria-label="Delete chat"
                className="grid size-6 shrink-0 place-items-center rounded text-muted opacity-0 transition-opacity hover:bg-white/[0.06] hover:text-danger focus-visible:opacity-100 group-hover:opacity-100"
              >
                <Trash2 className="size-3.5" />
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
