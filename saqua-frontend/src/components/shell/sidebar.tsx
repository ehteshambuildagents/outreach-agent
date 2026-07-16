"use client";

import Link from "next/link";
import { UserButton, useUser } from "@clerk/nextjs";
import { usePathname } from "next/navigation";
import { PanelLeftClose, Search } from "lucide-react";
import { NAV } from "@/lib/nav";
import { Logo } from "@/components/ui/logo";
import { cn } from "@/lib/utils";

function isActive(pathname: string, href: string) {
  if (href === "/dashboard") return pathname === "/dashboard";
  return pathname === href || pathname.startsWith(href + "/");
}

export function Sidebar({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const pathname = usePathname();
  const { user } = useUser();
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
            className="grid size-7 place-items-center rounded-md text-muted transition-colors hover:bg-white/[0.06] hover:text-text"
          >
            <Search className="size-[17px]" />
          </button>
          <button
            onClick={onToggle}
            aria-label="Collapse sidebar"
            title="Collapse sidebar"
            className="grid size-7 place-items-center rounded-md text-muted transition-colors hover:bg-white/[0.06] hover:text-text"
          >
            <PanelLeftClose className="size-[17px]" />
          </button>
        </div>

        <nav className="flex-1 overflow-y-auto px-3 py-2">
          {NAV.map((group, gi) => (
            <div key={gi} className="mb-1">
              {group.title && (
                <div className="px-3 pb-1.5 pt-4 text-[10px] font-semibold uppercase tracking-[0.12em] text-faint">
                  {group.title}
                </div>
              )}
              {group.items.map((item) => {
                const active = isActive(pathname, item.href);
                const Icon = item.icon;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    data-tour={item.href === "/campaigns" ? "nav-campaigns" : undefined}
                    className={cn(
                      "group relative mb-0.5 flex items-center gap-3 rounded-md px-3 py-[7px] text-sm transition-colors duration-150",
                      active
                        ? "bg-accent-soft text-text"
                        : "text-text-2 hover:bg-white/[0.04] hover:text-text",
                    )}
                  >
                    {active && (
                      <span className="absolute inset-y-1.5 left-0 w-0.5 rounded-full bg-accent" />
                    )}
                    <Icon
                      className={cn("size-[17px] shrink-0", active ? "text-accent-hi" : "text-muted group-hover:text-text-2")}
                    />
                    <span className="flex-1 truncate">{item.label}</span>
                    {item.badge && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-accent-soft px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-accent-hi">
                        <span className="size-1 animate-pulse-soft rounded-full bg-accent-hi" />
                        {item.badge}
                      </span>
                    )}
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>

        {/* User footer */}
        <div className="border-t border-border p-3">
          <div className="flex items-center gap-3 rounded-md px-2 py-1.5 hover:bg-white/[0.04]">
            <div className="grid size-8 place-items-center rounded-full border border-border bg-white/[0.06]">
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
                active ? "bg-accent-soft text-accent-hi" : "text-muted hover:bg-white/[0.04] hover:text-text-2",
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

