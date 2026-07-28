"use client";

import { motion, useReducedMotion } from "framer-motion";

import { useObserverBroken } from "./use-observer-health";

/**
 * Earned scroll reveal — content animates in the moment it enters the viewport,
 * once. Respects prefers-reduced-motion: reduced-motion users get the end state
 * instantly (no transform, no fade), never a delayed or hidden element.
 *
 * The hidden start state is server-rendered, so it must never be able to stick.
 * If the viewport observer turns out not to fire (see useObserverBroken), we
 * animate in on our own rather than leave the copy invisible.
 */
export function Reveal({
  children,
  delay = 0,
  className,
  y = 14,
}: {
  children: React.ReactNode;
  delay?: number;
  className?: string;
  y?: number;
}) {
  const reduced = useReducedMotion();
  const observerBroken = useObserverBroken();
  // Both of these mean "no scroll animation": render the finished state as plain
  // markup. `animate` cannot be used to escape here — while `whileInView` is
  // registered it owns these properties, so the element would stay at `initial`.
  if (reduced || observerBroken) return <div className={className}>{children}</div>;
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y, scale: 0.985 }}
      whileInView={{ opacity: 1, y: 0, scale: 1 }}
      viewport={{ once: true, margin: "-80px" }}
      transition={{ duration: 0.6, delay, ease: [0.22, 0.61, 0.36, 1] }}
    >
      {children}
    </motion.div>
  );
}
