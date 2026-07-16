"use client";

import { useEffect } from "react";

/**
 * Fixes the "UI freezes / looks like a screenshot after minimizing Chrome" bug.
 *
 * Two things go stale when the window is minimized/occluded and restored:
 *   1. `backdrop-filter` (glass) layers — Chrome's compositor can keep showing the
 *      last painted frame and never repaint them.
 *   2. framer-motion's requestAnimationFrame frameloop stops while the page is
 *      hidden and, if nothing schedules new work, doesn't repaint on restore.
 *
 * On restore we (a) briefly toggle a root class that drops `backdrop-filter` for two
 * frames, forcing the glass layers to re-composite, and (b) dispatch a resize event
 * to re-kick the motion frameloop and any layout-measuring effects. Cheap, runs only
 * on visibility transitions, and honors reduced motion (no visual animation added).
 */
export function VisibilityRepaint() {
  useEffect(() => {
    let raf1 = 0;
    let raf2 = 0;

    const repaint = () => {
      if (document.visibilityState !== "visible") return;
      const root = document.documentElement;
      root.classList.add("force-repaint");
      // Re-kick framer-motion's frameloop + re-measure viewport-dependent components.
      window.dispatchEvent(new Event("resize"));
      cancelAnimationFrame(raf1);
      cancelAnimationFrame(raf2);
      raf1 = requestAnimationFrame(() => {
        raf2 = requestAnimationFrame(() => root.classList.remove("force-repaint"));
      });
    };

    document.addEventListener("visibilitychange", repaint);
    window.addEventListener("focus", repaint);
    window.addEventListener("pageshow", repaint);
    return () => {
      document.removeEventListener("visibilitychange", repaint);
      window.removeEventListener("focus", repaint);
      window.removeEventListener("pageshow", repaint);
      cancelAnimationFrame(raf1);
      cancelAnimationFrame(raf2);
    };
  }, []);

  return null;
}
