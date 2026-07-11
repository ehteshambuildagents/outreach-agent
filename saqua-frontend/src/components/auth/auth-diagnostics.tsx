"use client";

import { useEffect } from "react";

function isAuthAsset(value: string) {
  return /clerk|turnstile|captcha|challenges\.cloudflare|recaptcha|google\.com\/recaptcha/i.test(value);
}

export function AuthDiagnostics() {
  useEffect(() => {
    function warn(message: string, details?: Record<string, unknown>) {
      console.warn(`[auth] ${message}`, details ?? {});
    }

    function onError(event: ErrorEvent) {
      const target = event.target;
      if (target instanceof HTMLScriptElement && isAuthAsset(target.src)) {
        warn("Auth script failed to load.", { src: target.src });
        return;
      }
      if (target instanceof HTMLIFrameElement && isAuthAsset(target.src)) {
        warn("Auth challenge iframe failed to load.", { src: target.src });
      }
    }

    function onUnhandledRejection(event: PromiseRejectionEvent) {
      const reason = String(event.reason?.message ?? event.reason ?? "");
      if (isAuthAsset(reason)) {
        warn("Auth runtime promise rejected.", { reason });
      }
    }

    function onSecurityPolicyViolation(event: SecurityPolicyViolationEvent) {
      const blocked = event.blockedURI || "";
      if (isAuthAsset(blocked)) {
        warn("Auth asset blocked by Content Security Policy.", {
          blockedURI: blocked,
          directive: event.effectiveDirective,
          originalPolicy: event.originalPolicy,
        });
      }
    }

    window.addEventListener("error", onError, true);
    window.addEventListener("unhandledrejection", onUnhandledRejection);
    document.addEventListener("securitypolicyviolation", onSecurityPolicyViolation);

    const timer = window.setTimeout(() => {
      if (!("Clerk" in window)) {
        warn("Clerk runtime did not initialize within 10 seconds.");
      }
    }, 10000);

    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("error", onError, true);
      window.removeEventListener("unhandledrejection", onUnhandledRejection);
      document.removeEventListener("securitypolicyviolation", onSecurityPolicyViolation);
    };
  }, []);

  return null;
}
