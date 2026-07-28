"use client";

import { useEffect, useState } from "react";

/**
 * Is IntersectionObserver actually delivering callbacks?
 *
 * Every scroll reveal on the marketing pages ships its content at opacity 0 and
 * waits for the observer to say "you are on screen". That bargain is only safe
 * if the observer answers. Some embedded webviews — in-app browsers inside
 * social apps, link preview panes, a few automation runtimes — expose the API
 * but never fire it, and there the page stays blank forever. Since a large share
 * of launch-day traffic arrives through exactly those in-app browsers, a silent
 * blank landing page is the worst failure the site has.
 *
 * Checking `typeof IntersectionObserver === "undefined"` does not catch this:
 * the constructor is present, it just never calls back. So we probe for real —
 * observe a node we know is on screen and see whether anything comes back.
 *
 * One probe serves the whole page: the result is cached at module scope and
 * every subscriber is notified, so N reveals cost one observer, not N.
 */

/** How long to wait for the probe before declaring the observer unusable. */
const PROBE_TIMEOUT_MS = 600;

type Health = "probing" | "ok" | "broken";

let health: Health = "probing";
let started = false;
const subscribers = new Set<(h: Health) => void>();

function settle(next: Health) {
  if (health !== "probing") return;
  health = next;
  for (const notify of subscribers) notify(next);
  subscribers.clear();
}

function startProbe() {
  if (started) return;
  started = true;

  if (typeof IntersectionObserver === "undefined") {
    settle("broken");
    return;
  }

  // <body> is on screen whenever the page is, so a working observer reports it
  // almost immediately. We only care that a callback arrives at all.
  try {
    const observer = new IntersectionObserver(() => {
      observer.disconnect();
      settle("ok");
    });
    observer.observe(document.body);
    window.setTimeout(() => {
      observer.disconnect();
      settle("broken");
    }, PROBE_TIMEOUT_MS);
  } catch {
    settle("broken");
  }
}

/**
 * True once we know the observer will not fire, so the caller should stop
 * waiting and show its content. Stays false on the happy path, which leaves the
 * normal scroll animation untouched in real browsers.
 */
export function useObserverBroken(): boolean {
  const [broken, setBroken] = useState(() => health === "broken");

  useEffect(() => {
    if (health === "broken") {
      setBroken(true);
      return;
    }
    if (health === "ok") return;

    const notify = (h: Health) => setBroken(h === "broken");
    subscribers.add(notify);
    startProbe();
    return () => {
      subscribers.delete(notify);
    };
  }, []);

  return broken;
}
