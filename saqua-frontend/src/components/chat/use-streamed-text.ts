"use client";

import { useEffect, useRef, useState } from "react";
import { useReducedMotion } from "framer-motion";

/**
 * Reveal a finished string progressively, word by word — the backend returns the
 * whole response in one blocking call, so this is a presentation-layer reveal, not
 * true token streaming. Reduced-motion users get the full text instantly.
 *
 * @param text   the final assistant text
 * @param active when false, the text is shown in full immediately (e.g. history)
 */
export function useStreamedText(text: string, active: boolean): { shown: string; done: boolean } {
  const reduced = useReducedMotion();
  const [count, setCount] = useState(active && !reduced ? 0 : Infinity);
  const words = useRef<string[]>([]);

  useEffect(() => {
    words.current = text.split(/(\s+)/); // keep whitespace tokens so spacing is exact
    if (!active || reduced) {
      setCount(Infinity);
      return;
    }
    setCount(0);
    let i = 0;
    const id = setInterval(() => {
      i += 2; // one word + its trailing space per tick
      setCount(i);
      if (i >= words.current.length) clearInterval(id);
    }, 28);
    return () => clearInterval(id);
  }, [text, active, reduced]);

  const shown = count === Infinity ? text : words.current.slice(0, count).join("");
  const done = count === Infinity || count >= words.current.length;
  return { shown, done };
}
