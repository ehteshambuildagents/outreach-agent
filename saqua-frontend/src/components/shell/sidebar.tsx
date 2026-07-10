"use client";

import Link from "next/link";
import { UserButton, useUser } from "@clerk/nextjs";
import { usePathname } from "next/navigation";
import { NAV } from "@/lib/nav";
import { cn } from "@/lib/utils";

function isActive(pathname: string, href: string) {
  if (href === "/dashboard") return pathname === "/dashboard";
  return pathname === href || pathname.startsWith(href + "/");
}

export function Sidebar() {
  const pathname = usePathname();
  const { user } = useUser();
  const displayName = user?.fullName || user?.primaryEmailAddress?.emailAddress || "Saqua user";
  const displayEmail = user?.primaryEmailAddress?.emailAddress || "Signed in";

  return (
    <>
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-[var(--rail-w)] flex-col border-r border-border bg-panel lg:flex">
        {/* Wordmark */}
        <div className="flex h-[var(--nav-h)] items-center gap-2.5 px-5">
          <LogoMark />
          <span className="text-[15px] font-semibold tracking-tight">Saqua</span>
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
            <div className="grid size-8 place-items-center rounded-full bg-gradient-to-br from-accent to-[#5b3fd6]">
              <UserButton afterSignOutUrl="/" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-xs font-medium text-text">{displayName}</div>
              <div className="truncate text-[11px] text-muted">{displayEmail}</div>
            </div>
          </div>
        </div>
      </aside>

      <nav className="fixed inset-x-3 bottom-3 z-40 grid grid-cols-5 rounded-xl border border-border bg-panel/95 p-1 shadow-pop backdrop-blur lg:hidden">
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

function LogoMark() {
  return (
    <div className="grid size-6 place-items-center rounded-md bg-accent text-white shadow-[0_2px_10px_-2px_var(--accent-line)]">
      <svg viewBox="0 0 24 24" className="size-3.5" fill="none">
        <path d="M12 2c2.5 3 6 4.5 6 9a6 6 0 1 1-12 0c0-4.5 3.5-6 6-9Z" fill="currentColor" />
      </svg>
    </div>
  );
}
