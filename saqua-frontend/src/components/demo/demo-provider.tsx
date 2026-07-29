"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";

/**
 * Demo-session awareness for the real app shell.
 *
 * A sandboxed demo visitor has no Clerk session; instead the backend set a
 * readable ``saqua_demo_exp`` cookie (expiry epoch only — no secret) alongside
 * the HttpOnly token. This provider reads that cookie to know whether the shell
 * is running in demo mode and, if so, fetches live turn status for the banner.
 *
 * For a real signed-in member the cookie is absent, so ``isDemo`` is false and
 * nothing here changes their experience.
 */

type DemoState = {
  isDemo: boolean;
  expiresAt: number | null; // epoch seconds
  turnsUsed: number;
  turnsLimit: number;
  /** Messages the visitor can still send. `null` when unknown (not a demo, or
   *  status not loaded yet) so callers can distinguish "unknown" from "zero". */
  turnsLeft: number | null;
  refresh: () => void;
  /** Optimistically count one turn the instant a message is sent, so the banner
   *  moves immediately; the next `refresh` reconciles to the server's truth. */
  noteTurnUsed: () => void;
  endSession: () => Promise<void>;
};

const DemoContext = createContext<DemoState>({
  isDemo: false,
  expiresAt: null,
  turnsUsed: 0,
  turnsLimit: 0,
  turnsLeft: null,
  refresh: () => {},
  noteTurnUsed: () => {},
  endSession: async () => {},
});

function readExpCookie(): number | null {
  if (typeof document === "undefined") return null;
  const m = document.cookie.match(/(?:^|;\s*)saqua_demo_exp=(\d+)/);
  if (!m) return null;
  const epoch = Number.parseInt(m[1], 10);
  return Number.isFinite(epoch) ? epoch : null;
}

export function DemoProvider({ children }: { children: React.ReactNode }) {
  const [expiresAt, setExpiresAt] = useState<number | null>(null);
  const [turnsUsed, setTurnsUsed] = useState(0);
  const [turnsLimit, setTurnsLimit] = useState(0);
  const [tick, setTick] = useState(0); // re-evaluates isDemo as the clock runs out

  // Stable identity: consumers put this in effect/callback dependency arrays
  // (the chat page calls it after every send), and a fresh function each render
  // would churn those callbacks. It only touches setters, so [] is correct.
  const refresh = useCallback(() => {
    const exp = readExpCookie();
    if (!exp || exp * 1000 <= Date.now()) {
      setExpiresAt(null);
      return;
    }
    setExpiresAt(exp);
    // Demo-only call: learn how many turns are left for the banner.
    void fetch("/api/demo/session", { cache: "no-store" })
      .then((r) => r.json())
      .then((d: { active?: boolean; expires_at?: number; turns_used?: number; turns_limit?: number }) => {
        if (d?.active) {
          setExpiresAt(d.expires_at ?? exp);
          setTurnsUsed(d.turns_used ?? 0);
          setTurnsLimit(d.turns_limit ?? 0);
        } else {
          setExpiresAt(null);
        }
      })
      .catch(() => {
        /* keep the cookie-derived expiry; status is a nice-to-have */
      });
  }, []);

  useEffect(() => {
    refresh();
    // Refetch each minute so the minutes-left countdown and the messages-left
    // count both stay honest as the session is used, and so an expired session
    // flips isDemo off on its own.
    const id = window.setInterval(() => {
      setTick((t) => t + 1);
      refresh();
    }, 60_000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Count one turn immediately on send. Clamped so it can never read negative
  // before the server reconciles; `refresh` (called right after) is authoritative.
  const noteTurnUsed = useCallback(() => {
    setTurnsUsed((u) => u + 1);
  }, []);

  const isDemo = expiresAt !== null && expiresAt * 1000 > Date.now();
  const turnsLeft = isDemo && turnsLimit > 0 ? Math.max(0, turnsLimit - turnsUsed) : null;
  void tick; // referenced so the interval re-render isn't optimised away

  const endSession = async () => {
    try {
      await fetch("/api/demo/session", { method: "DELETE", cache: "no-store" });
    } catch {
      /* best-effort; the cookie also expires on its own */
    }
    window.location.href = "/";
  };

  return (
    <DemoContext.Provider
      value={{ isDemo, expiresAt, turnsUsed, turnsLimit, turnsLeft, refresh, noteTurnUsed, endSession }}
    >
      {children}
    </DemoContext.Provider>
  );
}

export const useDemo = () => useContext(DemoContext);
