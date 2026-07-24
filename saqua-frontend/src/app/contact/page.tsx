"use client";

import { useState } from "react";
import { Check, Loader2, Mail, Sparkles } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Field, Input, Textarea } from "@/components/ui/field";
import { SiteNav } from "@/components/marketing/site-nav";
import { SiteFooter } from "@/components/marketing/site-footer";

const SUPPORT_EMAIL = "support@saqua.io";

type State = "idle" | "sending" | "done" | "error";

export default function ContactPage() {
  const [email, setEmail] = useState("");
  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");
  const [company, setCompany] = useState(""); // honeypot
  const [state, setState] = useState<State>("idle");
  const [error, setError] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (state === "sending") return;
    setState("sending");
    setError("");
    const res = await api.sendContact({ email, subject, message, company });
    if (res.ok) {
      setState("done");
      setSubject("");
      setMessage("");
    } else {
      setState("error");
      setError(res.error || `We couldn't send your message. Please email ${SUPPORT_EMAIL} directly.`);
    }
  }

  return (
    // No bg here: it would paint over the -z-10 glow (see globals.css .page-light).
    <main className="relative min-h-screen overflow-clip text-text">
      <div aria-hidden className="hero-glow-cool pointer-events-none absolute inset-x-0 top-0 -z-10 h-[560px]" />

      <SiteNav />

      <section className="mx-auto max-w-2xl px-6 pb-16 pt-36 text-center">
        <span className="float-soft inline-flex h-8 items-center gap-2 rounded-full border border-border bg-white px-4 text-xs font-medium shadow-[0_1px_2px_rgba(17,17,17,.04)]">
          <Sparkles className="size-3.5 text-accent" /> We usually reply within a day
        </span>
        <h1 className="mt-7 font-display text-4xl font-medium tracking-[-0.03em] md:text-6xl">
          Contact <span className="grad-text-anim">us</span>
        </h1>
        <p className="mx-auto mt-4 max-w-md text-base leading-7 text-muted">
          Questions, feedback, or need a hand? Send us a note and we&apos;ll reply to your inbox.
        </p>

        <Card className="mt-8 p-6 text-left shadow-card">
          <div className="flex items-center gap-3">
            <span className="grid size-10 place-items-center rounded-full bg-accent-soft text-accent">
              <Mail className="size-5" />
            </span>
            <div>
              <div className="text-sm text-muted">Prefer your own email app?</div>
              <a
                href={`mailto:${SUPPORT_EMAIL}`}
                className="text-base font-medium text-text transition-colors hover:text-accent"
              >
                {SUPPORT_EMAIL}
              </a>
            </div>
          </div>

          {state === "done" ? (
            <div
              className="mt-6 flex items-start gap-3 rounded-lg border border-accent-line bg-accent-soft p-5 text-left"
              role="status"
            >
              <Check className="mt-0.5 size-5 shrink-0 text-accent" />
              <div>
                <div className="text-sm font-semibold text-text">Message sent</div>
                <p className="mt-1 text-sm leading-6 text-text-2">
                  Thanks for reaching out. We&apos;ll reply to {email || "your inbox"} within a day.
                </p>
              </div>
            </div>
          ) : (
            <form onSubmit={submit} className="mt-6 space-y-4 border-t border-border-faint pt-6">
              {/* Honeypot. Hidden from people and assistive tech; bots fill it. */}
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
              <Field label="Your email">
                <Input
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@company.com"
                  disabled={state === "sending"}
                />
              </Field>
              <Field label="Subject">
                <Input
                  value={subject}
                  onChange={(e) => setSubject(e.target.value)}
                  placeholder="What's this about?"
                  disabled={state === "sending"}
                />
              </Field>
              <Field label="Message">
                <Textarea
                  required
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  placeholder="How can we help?"
                  disabled={state === "sending"}
                />
              </Field>
              <Button type="submit" variant="primary" className="w-full" disabled={state === "sending"}>
                {state === "sending" ? (
                  <>
                    <Loader2 className="size-4 animate-spin" /> Sending
                  </>
                ) : (
                  "Send message"
                )}
              </Button>
              {state === "error" && (
                <p className="text-center text-xs text-[color:var(--danger)]" role="alert">
                  {error}
                </p>
              )}
              <p className="text-center text-xs text-muted">
                We&apos;ll only use your email to reply to this message.
              </p>
            </form>
          )}
        </Card>
      </section>

      <SiteFooter />
    </main>
  );
}
