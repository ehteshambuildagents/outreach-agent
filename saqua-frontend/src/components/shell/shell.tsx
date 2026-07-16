"use client";

import { useCallback, useEffect, useState } from "react";
import { Sidebar } from "./sidebar";
import { Topbar } from "./topbar";
import { ApiAuthBridge } from "@/components/auth/api-auth-bridge";
import { cn } from "@/lib/utils";

const KEY = "saqua_rail_collapsed";

/** App shell that owns the sidebar collapse state (Claude-style minimize/maximize)
 * and shares it between the sidebar and the top bar, reflowing the main content. */
export function AppShell({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    try {
      setCollapsed(localStorage.getItem(KEY) === "1");
    } catch {
      /* storage blocked */
    }
  }, []);

  const toggle = useCallback(() => {
    setCollapsed((v) => {
      const nv = !v;
      try {
        localStorage.setItem(KEY, nv ? "1" : "0");
      } catch {
        /* ignore */
      }
      return nv;
    });
  }, []);

  return (
    <div className="min-h-screen">
      {/* Ambient depth behind the glass surfaces — muted indigo + teal blooms. */}
      <div aria-hidden className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
        <div className="bloom-indigo absolute -left-48 top-[-12%] size-[620px] rounded-full" />
        <div className="bloom-teal absolute right-[-16%] top-[28%] size-[560px] rounded-full" />
        <div className="bloom-indigo absolute bottom-[-18%] left-[38%] size-[520px] rounded-full opacity-70" />
      </div>
      <ApiAuthBridge />
      <Sidebar collapsed={collapsed} onToggle={toggle} />
      <div
        className={cn(
          "transition-[padding] duration-300 ease-smooth",
          collapsed ? "lg:pl-0" : "lg:pl-[var(--rail-w)]",
        )}
      >
        <Topbar collapsed={collapsed} onExpand={toggle} />
        <main className="mx-auto max-w-[1240px] px-5 pb-28 pt-6 md:px-8 md:py-8">{children}</main>
      </div>
    </div>
  );
}
