"use client";

import { useCallback, useEffect, useState } from "react";
import { Sidebar } from "./sidebar";
import { Topbar } from "./topbar";
import { ApiAuthBridge } from "@/components/auth/api-auth-bridge";
import { ChatNavProvider } from "@/components/chat/chat-nav";
import { DemoProvider } from "@/components/demo/demo-provider";
import { DemoBanner } from "@/components/demo/demo-banner";
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
    <DemoProvider>
    <ChatNavProvider>
      <div className="min-h-screen">
        {/* Ambient accent depth behind the glass — a wide, faint indigo wash across
            the top and a soft bloom anchored in the bottom-right corner, with a quiet
            left-side weight for balance. Decorative only: low opacity, soft-edged,
            never competes with content. */}
        <div aria-hidden className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
          <div className="bloom-indigo absolute left-1/2 top-[-18%] size-[760px] -translate-x-1/2 rounded-full" />
          <div className="bloom-teal absolute -bottom-40 right-[-14%] size-[620px] rounded-full" />
          <div className="bloom-indigo absolute left-[-11%] top-[34%] size-[460px] rounded-full opacity-60" />
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
          <DemoBanner />
          <main className="mx-auto max-w-[1240px] px-5 pb-28 pt-6 md:px-8 md:py-8">{children}</main>
        </div>
      </div>
    </ChatNavProvider>
    </DemoProvider>
  );
}
