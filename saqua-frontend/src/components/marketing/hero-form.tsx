"use client";

import { useState } from "react";
import { ArrowRight, Check, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

type State = "idle" | "sending" | "done" | "error";

/**
 * Pre-launch waitlist capture.
 *
 * This used to collect an address and then throw it away, routing to /sign-up. It
 * now actually POSTs to the waitlist, which is double opt-in: the success copy
 * says "check your email" because nobody is subscribed until they click the
 * confirmation link. Promising anything stronger here would be a lie.
 */
export function HeroForm({
  className,
  label = "Join the waitlist",
  tone = "solid",
  source = "hero",
}: {
  className?: string;
  label?: string;
  tone?: "solid" | "onGradient";
  source?: string;
}) {
  const [email, setEmail] = useState("");
  const [company, setCompany] = useState("");   // honeypot
  const [state, setState] = useState<State>("idle");
  const [error, setError] = useState("");
  const onGradient = tone === "onGradient";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (state === "sending") return;
    setState("sending");
    setError("");
    const res = await api.joinWaitlist({ email, source, company });
    if (res.ok) {
      setState("done");
      setEmail("");
    } else {
      setState("error");
      setError(res.error || "Something went wrong. Please try again.");
    }
  }

  if (state === "done") {
    return (
      <div
        className={cn(
          "mx-auto flex w-full max-w-sm items-center justify-center gap-2 rounded-sm px-4 py-3 text-sm font-medium",
          onGradient
            ? "border border-white/30 bg-white/15 text-white"
            : "border border-accent-line bg-accent-soft text-accent",
          className,
        )}
        role="status"
      >
        <Check className="size-4 shrink-0" />
        Check your email to confirm your spot.
      </div>
    );
  }

  return (
    <form onSubmit={submit} className={cn("mx-auto flex w-full max-w-sm flex-col gap-3", className)}>
      {/* Honeypot. Hidden from people and assistive tech; bots fill it anyway. */}
      <input
        type="text"
        name="company"
        value={company}
        onChange={(e) => setCompany(e.target.value)}
        tabIndex={-1}
        autoComplete="off"
        aria-hidden="true"
        className="pointer-events-none absolute left-[-9999px] size-0 opacity-0"
      />
      <input
        type="email"
        required
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        placeholder="Your work email"
        aria-label="Work email"
        disabled={state === "sending"}
        className={cn(
          "h-12 w-full rounded-sm px-4 text-sm outline-none transition-all disabled:opacity-60",
          onGradient
            ? "border border-white/30 bg-white/15 text-white placeholder:text-white/70 focus:border-white/60 focus:bg-white/25"
            : "border border-border-strong bg-white text-text shadow-[0_1px_2px_rgba(17,17,17,.04)] placeholder:text-faint focus:border-accent-line focus:shadow-[0_0_0_4px_var(--accent-soft)]",
        )}
      />
      <button
        type="submit"
        disabled={state === "sending"}
        className={cn(
          "inline-flex h-12 w-full items-center justify-center gap-2 rounded-sm text-sm font-semibold transition-all hover:-translate-y-px disabled:cursor-not-allowed disabled:opacity-70 disabled:hover:translate-y-0",
          onGradient
            ? "bg-white text-accent shadow-sm hover:shadow-md"
            : "bg-accent text-white shadow-[0_1px_2px_rgba(79,90,247,.35)] hover:bg-accent-hi hover:shadow-[0_8px_22px_rgba(79,90,247,.32)]",
        )}
      >
        {state === "sending" ? (
          <>
            <Loader2 className="size-4 animate-spin" /> Joining
          </>
        ) : (
          <>
            {label} <ArrowRight className="size-4" />
          </>
        )}
      </button>
      {state === "error" && (
        <p
          className={cn("text-xs", onGradient ? "text-white/90" : "text-[color:var(--danger)]")}
          role="alert"
        >
          {error}
        </p>
      )}
    </form>
  );
}
