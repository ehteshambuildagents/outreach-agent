"use client";

import { useEffect, useState } from "react";

/**
 * Live founding-offer countdown (gosollo "perks close in DD:HH:MM:SS"). Starts a
 * fixed window from first client mount; renders a stable value on first paint so
 * there is no hydration mismatch, then ticks every second.
 */
export function Countdown({ hours = 60 }: { hours?: number }) {
  const total = hours * 3_600_000;
  const [left, setLeft] = useState(total);

  useEffect(() => {
    const deadline = Date.now() + total;
    const tick = () => setLeft(Math.max(0, deadline - Date.now()));
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, [total]);

  const pad = (n: number) => String(n).padStart(2, "0");
  let d = left;
  const days = Math.floor(d / 86_400_000); d -= days * 86_400_000;
  const h = Math.floor(d / 3_600_000); d -= h * 3_600_000;
  const m = Math.floor(d / 60_000); d -= m * 60_000;
  const s = Math.floor(d / 1000);

  return (
    <div className="mt-4 font-mono text-xs text-muted">
      Founding pricing closes in{" "}
      <span className="font-medium text-text">
        {pad(days)} : {pad(h)} : {pad(m)} : {pad(s)}
      </span>
    </div>
  );
}
