"use client";

import { useCallback, useEffect, useState } from "react";
import { Sidebar } from "./sidebar";
import { Topbar } from "./topbar";
import { ApiAuthBridge } from "@/components/auth/api-auth-bridge";
import { ChatNavProvider } from "@/components/chat/chat-nav";
import { DemoProvider } from "@/components/demo/demo-provider";
import { DemoBanner } from "@/components/demo/demo-banner";
import { UpgradePrompt } from "@/components/billing/upgrade-prompt";
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
        {/* Fixed-height flex column: the chrome (top bar + demo banner) is flex-none,
            and `main` takes exactly the leftover height and owns the scroll. This is
            what lets a full-height page like Chat pin its composer to the bottom
            regardless of how tall the demo banner wraps — no viewport-height maths
            that forgets the banner and pushes the composer below the fold. */}
        <div
          className={cn(
            "app-shell-viewport flex flex-col bg-[#F8F8FB] transition-[padding] duration-300 ease-smooth",
            collapsed ? "lg:pl-0" : "lg:pl-[var(--rail-w)]",
          )}
        >
          <Topbar collapsed={collapsed} onExpand={toggle} />
          <DemoBanner />
          <UpgradePrompt />
          <main className="mx-auto w-full min-h-0 max-w-[1240px] flex-1 overflow-y-auto px-5 pb-28 pt-6 md:px-8 md:py-8">
            {children}
          </main>
        </div>
      </div>
    </ChatNavProvider>
    </DemoProvider>
  );
}
