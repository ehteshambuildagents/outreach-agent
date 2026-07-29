"""LIVE email-quality benchmark: generate real model emails for 20 varied
prospects and grade them. Complements the deterministic ``writer_benchmark`` (which
proves the GATE discriminates) by exercising the ACTUAL writer end to end.

This makes real Claude calls, so it is a SCRIPT, not a pytest test: run it against
a deployment (or locally with ANTHROPIC_API_KEY set):

    python -m agents.writer_live_benchmark

It writes one email per prospect, then reports:
  * per-email quality (overall / hook / reply-likelihood / spam risk), and how
    many fall below the weak threshold;
  * cross-PROSPECT repetition — 20 different companies must not yield near-identical
    emails (the "no identical sentence architecture across drafts" requirement at
    scale), checked with batch_distinctiveness.

Exit code is non-zero if any email is weak or the batch trips the repetition check,
so it can gate a release. No em dashes in the fixtures (house style).
"""

import sys

from agents import writer, writer_review
from services import claude_client

# 20 varied prospects across industries, personas, and signal shapes. Each is a
# research_company()-style envelope the writer accepts directly.
def _p(company, hook, customer, category, contact=None, role=None, extra=None):
    data = {
        "has_enough_detail": True, "company_name": company, "unique_hook": hook,
        "target_customer": customer, "product_category": category,
        "tone_style": "grounded, founder-led",
    }
    if contact:
        data["primary_contact_name"] = contact
        data["primary_contact_role"] = role or "Founder"
    if extra:
        data.update(extra)
    return {"status": "ok", "research_score": 72, "data": data}


PROSPECTS = [
    _p("Linear", "Shipped Linear for Agents; keyboard-first workflow", "software teams", "issue tracking", "Karri", "CEO"),
    _p("Ramp", "Launched treasury; auto-categorized receipts", "finance teams", "spend management", "Eric", "CEO"),
    _p("Vanta", "Automated SOC 2; 7000 customers", "security teams", "compliance automation", "Christina", "CEO"),
    _p("Deel", "Hiring in 150 countries; payroll rails", "HR teams", "global payroll", "Alex", "CEO"),
    _p("Rippling", "Unified HR and IT; device management", "ops teams", "workforce platform", "Parker", "CEO"),
    _p("Retool", "Internal tools in hours; new mobile builder", "engineering teams", "internal tooling", "David", "CEO"),
    _p("Census", "Reverse ETL; warehouse to tools sync", "data teams", "data activation", "Boris", "CEO"),
    _p("Mux", "Video API; new real-time streaming", "developers", "video infrastructure", "Jon", "CEO"),
    _p("Sardine", "Fraud prevention; instant onboarding", "risk teams", "fraud detection", "Soups", "CEO"),
    _p("Baseten", "Model serving; new autoscaling GPUs", "ML teams", "model inference", "Tuhin", "CEO"),
    _p("WorkOS", "Enterprise SSO in a day; directory sync", "developers", "enterprise auth", "Michael", "CEO"),
    _p("Modal", "Serverless GPUs; sub-second cold starts", "ML engineers", "compute platform", "Erik", "CEO"),
    _p("Neon", "Serverless Postgres; instant branching", "developers", "managed database", "Nikita", "CEO"),
    _p("Resend", "Email API for developers; React templates", "developers", "email infrastructure", "Zeno", "CEO"),
    _p("Clerk", "Auth and user management; new organizations", "developers", "authentication", "Colin", "CEO"),
    _p("Hex", "Notebooks and data apps; new AI cells", "analysts", "data collaboration", "Barry", "CEO"),
    _p("Metabase", "Self-serve BI; embedded analytics", "product teams", "business intelligence", "Sameer", "CEO"),
    _p("PostHog", "Product analytics; session replay added", "product teams", "product analytics", "James", "CEO"),
    _p("Cal.com", "Open scheduling; new routing forms", "revenue teams", "scheduling", "Peer", "CEO"),
    _p("Dub", "Open-source link management; analytics", "marketing teams", "link infrastructure", "Steven", "CEO"),
]


def run() -> int:
    bodies, rows, weak = [], [], 0
    print(f"Generating {len(PROSPECTS)} emails with the live writer...\n")
    for env in PROSPECTS:
        company = env["data"]["company_name"]
        try:
            out = writer.write_email(env)
        except claude_client.ClaudeClientError as exc:
            print(f"ABORTED: model unavailable ({exc}). Set ANTHROPIC_API_KEY / run "
                  "against a deployment.")
            return 2
        if out.get("status") != "ok" or not out.get("body"):
            rows.append((company, 0, 0, "n/a", "FAILED"))
            weak += 1
            continue
        q = writer_review.quality_report(out, env["data"])
        bodies.append(out["body"])
        is_weak = q["overall"] < writer_review.REVIEW_WEAK_THRESHOLD
        weak += 1 if is_weak else 0
        rows.append((company, q["overall"], q["reply_likelihood"], q["spam_risk"],
                     "WEAK" if is_weak else "ok"))

    print(f"{'company':<12} {'overall':>7} {'reply':>6} {'spam':>7}  verdict")
    print("-" * 48)
    for company, overall, reply, spam, verdict in rows:
        print(f"{company:<12} {overall:>7} {reply:>6} {spam:>7}  {verdict}")

    worst, pair = writer_review.batch_distinctiveness(bodies)
    print("-" * 48)
    print(f"weak emails: {weak}/{len(PROSPECTS)}")
    print(f"cross-prospect worst similarity: {worst:.2f} "
          f"-> {'REPETITIVE' if pair else 'varied'}"
          + (f" (pair {pair})" if pair else ""))

    ok = weak == 0 and pair is None
    print("\n" + ("PASS: every email is solid and the set is varied."
                  if ok else "FAIL: see WEAK rows / repetition above."))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run())
