"use client";

import Link from "next/link";
import { UserButton } from "@clerk/nextjs";
import { Bell, Plus, Search } from "lucide-react";
import { Button } from "@/components/ui/button";

export function Topbar() {
  return (
    <header className="glass-panel sticky top-0 z-20 flex h-[var(--nav-h)] items-center gap-3 border-b border-border px-5">
      <div className="relative hidden max-w-sm flex-1 items-center md:flex">
        <Search className="pointer-events-none absolute left-3 size-4 text-muted" />
        <input
          placeholder="Search campaigns, prospects, companies..."
          className="h-9 w-full rounded-sm border border-border bg-white/[0.02] pl-9 pr-3 text-sm text-text placeholder:text-muted transition-colors focus:border-border-strong focus:bg-white/[0.03] focus:outline-none"
        />
      </div>

      <div className="ml-auto flex items-center gap-2">
        <Button variant="ghost" size="icon" aria-label="Notifications" className="relative">
          <Bell className="size-[18px]" />
          <span className="absolute right-2 top-2 size-1.5 rounded-full bg-accent" />
        </Button>
        <Button asChild variant="primary" size="md">
          <Link href="/campaigns/new">
            <Plus className="size-4" /> New campaign
          </Link>
        </Button>
        <div className="ml-1 grid size-8 place-items-center rounded-full border border-border bg-white/[0.06] text-[11px] font-semibold text-text">
          <UserButton afterSignOutUrl="/" />
        </div>
      </div>
    </header>
  );
}
