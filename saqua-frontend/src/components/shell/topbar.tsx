"use client";

import { UserButton } from "@clerk/nextjs";
import { Search, PanelLeftOpen } from "lucide-react";

export function Topbar({ collapsed, onExpand }: { collapsed: boolean; onExpand: () => void }) {
  return (
    <header className="glass-panel sticky top-0 z-20 flex h-[var(--nav-h)] items-center gap-3 border-b border-border px-5">
      {/* Reopen button — only when the rail is collapsed (Claude-style) */}
      {collapsed && (
        <button
          onClick={onExpand}
          aria-label="Open sidebar"
          title="Open sidebar"
          className="hidden size-8 place-items-center rounded-md text-muted transition-colors hover:bg-hover hover:text-text lg:grid"
        >
          <PanelLeftOpen className="size-[18px]" />
        </button>
      )}
      <div className="relative hidden max-w-sm flex-1 items-center md:flex">
        <Search className="pointer-events-none absolute left-3 size-4 text-muted" />
        <input
          id="topbar-search"
          placeholder="Search campaigns, prospects, companies..."
          className="h-9 w-full rounded-lg border border-border bg-card pl-9 pr-3 text-sm text-text placeholder:text-muted transition-colors focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent-soft"
        />
      </div>

      <div className="ml-auto flex items-center gap-2">
        <div className="ml-1 grid size-8 place-items-center rounded-full border border-border bg-card text-[11px] font-semibold text-text">
          <UserButton afterSignOutUrl="/" />
        </div>
      </div>
    </header>
  );
}
