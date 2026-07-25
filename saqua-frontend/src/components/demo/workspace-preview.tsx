import {
  Megaphone,
  MessageSquare,
  SendHorizonal,
  Settings,
  ShieldCheck,
  Users,
} from "lucide-react";
import { BrowserFrame } from "@/components/ui/browser-frame";
import { Logo } from "@/components/ui/logo";

/**
 * A non-interactive, illustrative preview of the /ai workspace, shown on /demo
 * ABOVE the email step so the visitor wants in before being asked for anything.
 * Built from the real product's visual language (same tokens, same layout
 * grammar: rail, chat, scored card, serif draft) rather than a screenshot, so it
 * stays true across theme changes. All content is clearly illustrative; the
 * bottom of the frame dissolves into the canvas and the gate card overlaps it,
 * which is what makes the page read as a threshold into the product rather than
 * an email form.
 */
export function WorkspacePreview() {
  return (
    <div aria-hidden className="pointer-events-none select-none">
      <BrowserFrame url="saqua.io/ai" className="mx-auto max-w-4xl">
        <div className="flex h-[400px] overflow-hidden md:h-[440px]">
          {/* ── Rail (hidden on small screens; the chat carries the story) ── */}
          <div className="hidden w-44 shrink-0 flex-col border-r border-border-faint bg-panel/50 px-3 py-4 md:flex">
            <div className="flex items-center gap-2 px-1.5 font-display text-sm font-semibold text-text">
              <Logo className="h-4 w-auto" /> Saqua
            </div>
            <div className="mt-5 space-y-1 text-[12px] font-medium">
              <div className="flex items-center gap-2 rounded-md bg-accent-soft px-2.5 py-1.5 text-accent">
                <MessageSquare className="size-3.5" /> Chat
              </div>
              <div className="flex items-center gap-2 rounded-md px-2.5 py-1.5 text-text-2">
                <Users className="size-3.5" /> Prospects
              </div>
              <div className="flex items-center gap-2 rounded-md px-2.5 py-1.5 text-text-2">
                <Megaphone className="size-3.5" /> Campaigns
              </div>
              <div className="flex items-center gap-2 rounded-md px-2.5 py-1.5 text-text-2">
                <Settings className="size-3.5" /> Settings
              </div>
            </div>
            <div className="mt-auto rounded-lg border border-border-faint bg-white/60 px-2.5 py-2 text-[10px] text-muted">
              Demo visitor · sandboxed
            </div>
          </div>

          {/* ── Chat column ─────────────────────────────────────────────── */}
          <div className="flex min-w-0 flex-1 flex-col">
            <div className="flex-1 space-y-3.5 overflow-hidden px-4 py-5 md:px-6">
              {/* Visitor ask */}
              <div className="flex justify-end">
                <div className="max-w-[85%] rounded-2xl rounded-br-md bg-text px-3.5 py-2 text-[12px] leading-5 text-white shadow-sm">
                  Find B2B fintech startups that just raised a seed round
                </div>
              </div>

              {/* Agent working */}
              <div className="flex items-center gap-2 text-[11px] text-muted">
                <span className="size-1.5 animate-pulse rounded-full bg-accent motion-reduce:animate-none" />
                Researching 4 companies from live sources…
              </div>

              {/* Scored prospect card */}
              <div className="max-w-md rounded-xl border border-border bg-card p-3.5 shadow-card">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-[13px] font-semibold text-text">Driftlake</div>
                    <div className="truncate font-mono text-[10px] text-muted">driftlake.io · Maya Chen, Co-founder</div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    <span className="grid size-8 place-items-center rounded-full border-2 border-accent text-[11px] font-bold text-accent">
                      86
                    </span>
                    <span className="text-[10px] font-semibold text-[color:var(--success)]">Strong fit</span>
                  </div>
                </div>
                <div className="mt-2.5 rounded-lg bg-black/[0.03] px-3 py-2 text-[11px] leading-4 text-text-2">
                  <span className="font-medium text-text">Signal:</span> &ldquo;Hiring our first AE as we
                  scale past 40 customers&rdquo;
                  <span className="ml-1.5 font-mono text-[9px] text-muted">driftlake.io/careers</span>
                </div>
              </div>

              {/* Guard-checked draft, in the product's serif */}
              <div className="max-w-md rounded-xl border border-border bg-card p-3.5 shadow-card">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[10px] font-semibold uppercase tracking-wide text-muted">
                    Draft · your first AE hire
                  </span>
                  <span className="inline-flex items-center gap-1 rounded-full bg-[color:var(--success-soft)] px-2 py-0.5 text-[9px] font-semibold text-[color:var(--success)]">
                    <ShieldCheck className="size-2.5" /> Guard passed
                  </span>
                </div>
                <p className="mt-2 font-serif text-[13px] leading-5 text-text">
                  Maya, hiring your first AE while the founders still close every deal is a real
                  turning point. Curious how you&apos;re deciding what they inherit first…
                </p>
              </div>
            </div>

            {/* Composer */}
            <div className="border-t border-border-faint px-4 py-3 md:px-6">
              <div className="flex items-center justify-between gap-3 rounded-xl border border-border-strong bg-white px-3.5 py-2.5 text-[12px] text-faint shadow-[0_1px_2px_rgba(17,17,17,.04)]">
                Message Saqua…
                <span className="grid size-6 place-items-center rounded-md bg-accent text-white">
                  <SendHorizonal className="size-3" />
                </span>
              </div>
            </div>
          </div>
        </div>
      </BrowserFrame>
    </div>
  );
}
