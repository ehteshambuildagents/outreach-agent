"use client";

import { useState } from "react";
import Link from "next/link";
import { Mail } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Logo } from "@/components/ui/logo";
import { Field, Input, Textarea } from "@/components/ui/field";

const SUPPORT_EMAIL = "support@saqua.io";

export default function ContactPage() {
  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");
  const mailto = `mailto:${SUPPORT_EMAIL}?subject=${encodeURIComponent(
    subject || "Saqua — hello",
  )}&body=${encodeURIComponent(message)}`;

  return (
    <main className="relative min-h-screen overflow-hidden bg-bg text-text">
      <div aria-hidden className="pointer-events-none absolute inset-0 -z-10">
        <div className="bloom-indigo animate-drift absolute -left-40 -top-40 size-[560px] rounded-full" />
        <div className="bloom-teal animate-drift-2 absolute -right-48 top-[40%] size-[520px] rounded-full opacity-70" />
      </div>

      <header className="mx-auto flex h-16 max-w-4xl items-center justify-between px-6">
        <Link href="/" className="flex items-center gap-2 text-sm font-semibold">
          <Logo className="h-6 w-auto" /> Saqua
        </Link>
        <Button asChild variant="secondary" size="sm">
          <Link href="/pricing">Pricing</Link>
        </Button>
      </header>

      <section className="mx-auto max-w-2xl px-6 py-16">
        <h1 className="text-4xl font-semibold tracking-tight md:text-5xl">Contact us</h1>
        <p className="mt-4 text-sm leading-6 text-text-2">
          Questions, feedback, or need a hand? Reach the team directly — we usually reply within a day.
        </p>

        <Card className="mt-8 p-6">
          <div className="flex items-center gap-3">
            <span className="grid size-10 place-items-center rounded-md bg-accent-soft text-accent-hi">
              <Mail className="size-5" />
            </span>
            <div>
              <div className="text-sm text-muted">Email us</div>
              <a
                href={`mailto:${SUPPORT_EMAIL}`}
                className="text-base font-medium text-text transition-colors hover:text-accent-hi"
              >
                {SUPPORT_EMAIL}
              </a>
            </div>
          </div>

          <div className="mt-6 space-y-4 border-t border-border-faint pt-6">
            <Field label="Subject">
              <Input
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                placeholder="What's this about?"
              />
            </Field>
            <Field label="Message">
              <Textarea
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder="How can we help?"
              />
            </Field>
            <Button asChild variant="primary" className="w-full">
              <a href={mailto}>Send email</a>
            </Button>
            <p className="text-center text-xs text-muted">
              This opens your email app addressed to {SUPPORT_EMAIL}.
            </p>
          </div>
        </Card>
      </section>
    </main>
  );
}
