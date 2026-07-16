"use client";

import { useState } from "react";
import { motion, AnimatePresence, useReducedMotion } from "framer-motion";
import { Send } from "lucide-react";
import { Logo } from "./logo";
import { cn } from "@/lib/utils";

const EASE = [0.22, 0.61, 0.36, 1] as const;

// Cards unfold from the mark and fan DOWN-and-out into the hero whitespace (never
// up into the nav). Angles/distances are deliberately uneven so it reads organic,
// not like a uniform burst.
const CARDS = [
  { x: -46, y: 24, delay: 0.06, rot: -7 },
  { x: 44, y: 30, delay: 0.13, rot: 6 },
  { x: -22, y: 52, delay: 0.2, rot: -4 },
  { x: 32, y: 56, delay: 0.27, rot: 8 },
];

/**
 * The signature brand moment. On HOVER (never click), the mark gently reacts, then
 * unfolds into message bubbles that morph into little email cards, they float, a
 * paper plane launches, everything eases back into the mark, and two status badges
 * ("Replied!" then "Sent!") settle beside it. Lightweight (transforms + opacity
 * only) and fully reversible on mouse-leave. Reduced-motion users get the plain
 * mark with no animation. Purely decorative — pointer-events are off so the parent
 * link still works.
 */
export function AnimatedLogo({
  className,
  markClassName,
}: {
  className?: string;
  markClassName?: string;
}) {
  const reduced = useReducedMotion();
  const [hovered, setHovered] = useState(false);
  const active = hovered && !reduced;

  return (
    <div
      className={cn("relative inline-flex items-center", className)}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <motion.span
        animate={active ? { scale: [1, 0.9, 1.05, 1], rotate: [0, -4, 2, 0] } : { scale: 1, rotate: 0 }}
        transition={{ duration: 0.7, ease: EASE }}
        className="relative z-10"
      >
        <Logo className={markClassName} />
      </motion.span>

      {!reduced && (
        <AnimatePresence>
          {hovered && (
            <div className="pointer-events-none absolute left-1/2 top-1/2 z-0" aria-hidden>
              {/* message bubbles -> email cards, unfolding + floating */}
              {CARDS.map((c, i) => (
                <motion.div
                  key={i}
                  className="absolute -translate-x-1/2 -translate-y-1/2"
                  initial={{ x: 0, y: 0, scale: 0.3, opacity: 0, borderRadius: 999 }}
                  animate={{
                    x: c.x,
                    y: c.y,
                    scale: 1,
                    opacity: 1,
                    rotate: c.rot,
                    borderRadius: 7,
                    transition: { duration: 0.5, delay: c.delay, ease: EASE },
                  }}
                  exit={{
                    x: 0,
                    y: 0,
                    scale: 0.3,
                    opacity: 0,
                    borderRadius: 999,
                    transition: { duration: 0.34, ease: EASE },
                  }}
                >
                  <motion.div
                    animate={{ y: [0, -3, 0] }}
                    transition={{ duration: 2.6, delay: c.delay, repeat: Infinity, ease: "easeInOut" }}
                    className="glass flex w-11 flex-col gap-1 rounded-[7px] border border-accent-line/50 p-1.5 shadow-pop"
                  >
                    <span className="h-1 w-6 rounded-full bg-accent/70" />
                    <span className="h-1 w-8 rounded-full bg-white/25" />
                    <span className="h-1 w-4 rounded-full bg-white/15" />
                  </motion.div>
                </motion.div>
              ))}

              {/* paper plane launches on a small arc, then fades */}
              <motion.span
                className="absolute -translate-x-1/2 -translate-y-1/2 text-accent-hi"
                initial={{ x: 0, y: 0, opacity: 0, scale: 0.5, rotate: -24 }}
                animate={{
                  x: [0, 26, 60],
                  y: [0, 6, 40],
                  opacity: [0, 1, 0],
                  scale: [0.5, 1, 0.9],
                  rotate: [-24, 8, 22],
                  transition: { duration: 0.95, delay: 0.32, ease: EASE },
                }}
                exit={{ opacity: 0 }}
              >
                <Send className="size-4" />
              </motion.span>

              {/* status badges settle beside the mark */}
              <motion.div
                className="absolute left-[10px] top-[38px] whitespace-nowrap"
                initial={{ opacity: 0, y: 8, scale: 0.9 }}
                animate={{ opacity: 1, y: 0, scale: 1, transition: { duration: 0.35, delay: 0.55, ease: EASE } }}
                exit={{ opacity: 0, y: 4, transition: { duration: 0.22 } }}
              >
                <span className="inline-flex items-center gap-1 rounded-full border border-accent-line bg-accent-soft px-2 py-0.5 text-[10px] font-medium text-accent-hi shadow-pop">
                  <span className="size-1.5 animate-pulse-soft rounded-full bg-accent" /> Replied!
                </span>
              </motion.div>
              <motion.div
                className="absolute left-[20px] top-[62px] whitespace-nowrap"
                initial={{ opacity: 0, y: 8, scale: 0.9 }}
                animate={{ opacity: 1, y: 0, scale: 1, transition: { duration: 0.35, delay: 0.8, ease: EASE } }}
                exit={{ opacity: 0, y: 4, transition: { duration: 0.22 } }}
              >
                <span className="inline-flex items-center gap-1 rounded-full border border-border bg-card/80 px-2 py-0.5 text-[10px] font-medium text-text-2 shadow-pop">
                  <Send className="size-2.5 text-accent-hi" /> Sent!
                </span>
              </motion.div>
            </div>
          )}
        </AnimatePresence>
      )}
    </div>
  );
}
