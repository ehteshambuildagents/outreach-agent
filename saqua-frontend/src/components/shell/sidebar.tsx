"use client";

import Link from "next/link";
import { UserButton, useUser } from "@clerk/nextjs";
import { usePathname } from "next/navigation";
import { PanelLeftClose, Search, PenSquare, MessageSquare, Trash2 } from "lucide-react";
import { NAV } from "@/lib/nav";
import { Logo } from "@/components/ui/logo";
import { useChatNav } from "@/components/chat/chat-nav";
import { cn } from "@/lib/utils";

function isActive(pathname: string, href: string) {
  if (href === "/dashboard") return pathname === "/dashboard";
  return pathname === href || pathname.startsWith(href + "/");
}

export function Sidebar({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const pathname = usePathname();
  const { user } = useUser();
  const { conversations, activeId, open, startNew, remove } = useChatNav();
  const onChat = pathname === "/ai";
  const displayName = user?.fullName || user?.primaryEmailAddress?.emailAddress || "Saqua user";
  const displayEmail = user?.primaryEmailAddress?.emailAddress || "Signed in";

  return (
    <>
      <aside
        className={cn(
          "glass-panel fixed inset-y-0 left-0 z-30 hidden w-[var(--rail-w)] flex-col border-r border-border transition-transform duration-300 ease-smooth lg:flex",
          collapsed && "lg:-translate-x-full",
        )}
      >
        {/* Header — wordmark left; search + collapse toggle top-right (Claude layout) */}
        <div className="flex h-[var(--nav-h)] items-center gap-2 px-4">
          <Logo className="h-5 w-auto" />
          <span className="flex-1 truncate text-[15px] font-semibold tracking-tight">Saqua</span>
          <button
            onClick={() => document.getElementById("topbar-search")?.focus()}
            aria-label="Search"
            className="grid size-7 place-items-center rounded-md text-muted transition-colors hover:bg-black/[0.05] hover:text-text"
          >
            <Search className="size-[17px]" />
          </button>
          <button
            onClick={onToggle}
            aria-label="Collapse sidebar"
            title="Collapse sidebar"
            className="grid size-7 place-items-center rounded-md text-muted transition-colors hover:bg-black/[0.05] hover:text-text"
          >
            <PanelLeftClose className="size-[17px]" />
          </button>
        </div>

        {/* Page nav */}
        <nav className="px-3 pb-1 pt-1">
          {NAV[0].items.map((item) => {
            const active = isActive(pathname, item.href);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                data-tour={item.href === "/campaigns" ? "nav-campaigns" : undefined}
                className={cn(
                  "group relative mb-0.5 flex items-center gap-3 rounded-md px-3 py-[7px] text-sm transition-colors duration-150",
                  active ? "bg-accent-soft text-text" : "text-text-2 hover:bg-black/[0.04] hover:text-text",
                )}
              >
                {active && <span className="absolute inset-y-1.5 left-0 w-0.5 rounded-full bg-accent" />}
                <Icon className={cn("size-[17px] shrink-0", active ? "text-accent" : "text-muted group-hover:text-text-2")} />
                <span className="flex-1 truncate">{item.label}</span>
              </Link>
            );
          })}
        </nav>

        {/* Chat history — beneath the nav, ChatGPT-style compose (new chat) top-right */}
        <div data-tour="rail" className="mt-3 flex items-center justify-between border-t border-border-faint px-4 pb-1 pt-3">
          <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-faint">Recents</span>
          <button
            onClick={startNew}
            aria-label="New chat"
            title="New chat"
            className="grid size-6 place-items-center rounded text-muted transition-colors hover:bg-black/[0.05] hover:text-text"
          >
            <PenSquare className="size-3.5" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
          {conversations.length === 0 ? (
            <p className="px-3 py-4 text-center text-[11px] leading-4 text-muted">
              No chats yet. Start one from Chat.
            </p>
          ) : (
            conversations.map((t) => (
              <div
                key={t.id}
                className={cn(
                  "group mb-0.5 flex items-center gap-2.5 rounded-md px-3 py-1.5 text-sm transition-colors",
                  onChat && t.id === activeId ? "bg-accent-soft text-text" : "text-text-2 hover:bg-black/[0.04] hover:text-text",
                )}
              >
                <button onClick={() => open(t.id)} className="flex min-w-0 flex-1 items-center gap-2.5 text-left">
                  <MessageSquare
                    className={cn("size-3.5 shrink-0", onChat && t.id === activeId ? "text-accent" : "text-muted")}
                  />
                  <span className="truncate">{t.title || "New chat"}</span>
                </button>
                <button
                  onClick={() => remove(t.id)}
                  aria-label="Delete chat"
                  className="grid size-6 shrink-0 place-items-center rounded text-muted opacity-0 transition-opacity hover:bg-black/[0.05] hover:text-danger focus-visible:opacity-100 group-hover:opacity-100"
                >
                  <Trash2 className="size-3.5" />
                </button>
              </div>
            ))
          )}
        </div>

        {/* User footer */}
        <div className="border-t border-border p-3">
          <div className="flex items-center gap-3 rounded-md px-2 py-1.5 hover:bg-black/[0.04]">
            <div className="grid size-8 place-items-center rounded-full border border-border bg-black/[0.04]">
              <UserButton afterSignOutUrl="/" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-xs font-medium text-text">{displayName}</div>
              <div className="truncate text-[11px] text-muted">{displayEmail}</div>
            </div>
          </div>
        </div>
      </aside>

      <nav className="glass-panel fixed inset-x-3 bottom-3 z-40 grid grid-cols-6 rounded-xl border border-border p-1 shadow-pop lg:hidden">
        {NAV[0].items.map((item) => {
          const active = isActive(pathname, item.href);
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-label={item.label}
              className={cn(
                "flex min-w-0 flex-col items-center justify-center gap-1 rounded-lg px-1 py-2 text-[10px] transition-colors",
                active ? "bg-accent-soft text-accent" : "text-muted hover:bg-black/[0.04] hover:text-text-2",
              )}
            >
              <Icon className="size-4" />
              <span className="w-full truncate text-center">{item.label}</span>
            </Link>
          );
        })}
      </nav>
    </>
  );
}
