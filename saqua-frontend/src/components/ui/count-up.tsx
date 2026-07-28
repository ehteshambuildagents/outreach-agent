"use client";

import { useEffect, useRef, useState } from "react";
import { useInView, useReducedMotion } from "framer-motion";

import { useObserverBroken } from "./use-observer-health";

/**
 * A number that ticks up to its value the first time it scrolls into view.
 *
 * Used for fit scores, where the count carries meaning: a score that lands
 * rather than simply being printed reads as something Saqua worked out. Digits
 * are tabular so the surrounding row never reflows as it counts.
 *
 * Reduced motion gets the final value immediately.
 */
export function CountUp({
  to,
  duration = 900,
  className,
}: {
  to: number;
  duration?: number;
  className?: string;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true, margin: "-40px" });
  const reduced = useReducedMotion();
  const observerBroken = useObserverBroken();
  const [n, setN] = useState(0);

  useEffect(() => {
    // Fail safe to the real number. A score is content, not decoration: if we
    // cannot observe the scroll (reduced motion, or an observer that never
    // fires) the badge must still read 92, never a stuck "0". Testing for a
    // missing IntersectionObserver is not enough — the webviews that break this
    // do have the constructor, they just never call back.
    if (reduced || observerBroken) {
      setN(to);
      return;
    }
    if (!inView) return;
    let raf = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      // Cubic ease-out: it settles onto the number instead of stopping dead.
      setN(Math.round(to * (1 - Math.pow(1 - t, 3))));
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [inView, reduced, observerBroken, to, duration]);

  return (
    <span ref={ref} className={className} style={{ fontVariantNumeric: "tabular-nums" }}>
      {n}
    </span>
  );
}
