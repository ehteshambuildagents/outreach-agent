"use client";

import { motion, useReducedMotion } from "framer-motion";
import { Logo } from "./logo";
import { cn } from "@/lib/utils";

const EASE = [0.22, 0.61, 0.36, 1] as const;

/**
 * The brand mark's micro-interaction. Deliberately quiet: on hover the mark lifts
 * on a soft spring and a single accent halo blooms behind it, then settles. No
 * bursts, no props flying — just a small, modern "alive" cue. Fully reversible and
 * disabled under prefers-reduced-motion. Purely decorative: pointer-events are off
 * so the parent link still owns the click.
 */
export function AnimatedLogo({
  className,
  markClassName,
}: {
  className?: string;
  markClassName?: string;
}) {
  const reduced = useReducedMotion();

  if (reduced) {
    return (
      <span className={cn("relative inline-flex items-center", className)}>
        <Logo className={markClassName} />
      </span>
    );
  }

  return (
    <motion.span
      className={cn("group relative inline-flex items-center", className)}
      initial="rest"
      whileHover="hover"
      animate="rest"
    >
      {/* accent halo — blooms once on hover, sits behind the mark */}
      <motion.span
        aria-hidden
        className="pointer-events-none absolute left-1/2 top-1/2 -z-10 h-10 w-10 -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent/25 blur-lg"
        variants={{
          rest: { opacity: 0, scale: 0.6 },
          hover: { opacity: 1, scale: 1.15 },
        }}
        transition={{ duration: 0.45, ease: EASE }}
      />
      <motion.span
        className="relative block"
        variants={{
          rest: { scale: 1, y: 0, rotate: 0 },
          hover: { scale: 1.08, y: -1, rotate: -3 },
        }}
        transition={{ type: "spring", stiffness: 320, damping: 18 }}
      >
        <Logo className={markClassName} />
      </motion.span>
    </motion.span>
  );
}
