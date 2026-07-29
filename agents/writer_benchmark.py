"""A labelled benchmark for the deterministic email-quality gate.

The writer's quality gate (``writer_review``) is only trustworthy if it actually
tells a real founder email apart from the failure modes cold email falls into: AI
throat-clearing, templated founder-outbound boilerplate, personalization that
isn't grounded in the research, and batches where every message is the same email
reskinned. This module pins a small corpus of each, labelled, so a test can prove
the gate SEPARATES them, and so a "before/after" is reportable without a model
call (run ``python -m agents.writer_benchmark`` to print the table).

Everything here is offline and deterministic: no Claude call, no network. The
emails obey the house style (no em dashes).
"""

from agents import writer_review as reviewer

# Grounded research the STRONG emails personalize on. The generic/unsupported
# examples deliberately fail to reference these, which is the point.
_LINEAR = {
    "company_name": "Linear", "product_category": "issue tracking",
    "unique_hook": "keyboard-first workflow", "recent_focus": "Linear for Agents",
    "notable_customers": ["Ramp", "Vercel"], "metrics_or_traction": "10,000 teams",
    "primary_contact_name": "Karri",
}
_RAMP = {
    "company_name": "Ramp", "product_category": "spend management",
    "unique_hook": "auto-categorized receipts", "recent_focus": "treasury launch",
    "notable_customers": ["Shopify"], "metrics_or_traction": "25,000 customers",
    "primary_contact_name": "Eric",
}

# ── The corpus: (category, subject, body, grounded_data) ───────────────────
STRONG = [
    ("linear-agents", "Linear for Agents",
     "Karri, saw Linear shipped Linear for Agents. The keyboard-first flow is why "
     "our eng team practically lives in Linear, so giving agents that same speed is "
     "a sharp call. We've been cutting triage time for Series A teams without "
     "breaking that workflow. Want the two-line version of how Ramp runs it?",
     _LINEAR),
    ("ramp-treasury", "your treasury launch",
     "Eric, the treasury launch caught my eye. Ramp already auto-categorizes "
     "receipts better than anything Shopify's finance team had before, so moving "
     "into treasury is a natural pull. We help finance-led teams close the books a "
     "few days faster. Worth me sending one concrete example?",
     _RAMP),
]

GENERIC_AI = [
    ("hope-well", "Quick question",
     "Hi there, I hope this email finds you well. My name is Alex and I am reaching "
     "out because I came across your company and was really impressed. I wanted to "
     "see if you would be open to a quick call to explore synergies. Let me know "
     "your thoughts. Looking forward to hearing from you.",
     _LINEAR),
    ("came-across", "Reaching out",
     "Hello, I hope you are doing well. I noticed your company online and thought "
     "there might be a great opportunity for us to work together. I would love to "
     "hop on a quick 30-minute call to discuss how we can help you grow. Are you "
     "the right person for this?",
     _RAMP),
]

TEMPLATED = [
    ("founder-boiler", "founders and replies",
     "Hey, we help founders get replies without spending hours on the "
     "personalization grind. Generic templates don't work anymore, so we write "
     "personalized cold email that gets founders actual replies. It saves hours "
     "per prospect. Worth exploring?",
     _LINEAR),
]

# Personalization that SOUNDS specific but references nothing in the research.
UNSUPPORTED = [
    ("made-up", "Impressive growth",
     "Karri, your recent Series C and the expansion into Latin America are "
     "seriously impressive, and the new office in Berlin shows real ambition. We "
     "help hypergrowth companies like yours scale outbound. Thoughts?",
     _LINEAR),
]

# A batch where every step is the same email reskinned (the repetition failure).
REPETITIVE_BATCH = [
    "Karri, saw the keyboard-first workflow and it's sharp. We help Series A teams "
    "cut triage time without breaking that flow. Worth a quick look?",
    "Karri, saw the keyboard-first workflow and it's really sharp. We help Series A "
    "teams cut their triage time without breaking that flow. Worth a look?",
    "Karri, the keyboard-first workflow is sharp. We help Series A teams cut triage "
    "time without breaking the flow. Worth a quick look this week?",
]
# A batch that varies angle, opening, and shape (should NOT be flagged).
VARIED_BATCH = [
    "Karri, saw Linear for Agents. Handing agents the keyboard-first speed is a "
    "sharp call. We've cut triage time for teams like Ramp. Want the short version?",
    "The thing I keep coming back to on Linear is how little it gets in the way. "
    "That's rare. If you're rethinking triage as agents come in, I've got one idea "
    "that fits your workflow. Open to it?",
    "Quick one, Karri. Vercel and Ramp both standardized on Linear for a reason. "
    "We help the next cohort of Series A teams get there without the migration "
    "pain. Should I send a two-line teardown?",
]


def score_corpus() -> dict:
    """Score every labelled example with the real gate. Returns
    {category: [(name, score, weak, top_issue)]}."""
    out = {}
    for cat, items in (("strong", STRONG), ("generic_ai", GENERIC_AI),
                       ("templated", TEMPLATED), ("unsupported", UNSUPPORTED)):
        rows = []
        for name, subject, body, data in items:
            rev = reviewer.review({"subject": subject, "body": body}, data)
            rows.append((name, rev.score, rev.weak,
                         rev.issues[0] if rev.issues else ""))
        out[cat] = rows
    return out


def report() -> str:
    """A human-readable before/after-style table of the gate's verdicts."""
    scored = score_corpus()
    lines = ["category      score  weak?  example",
             "-" * 60]
    for cat in ("strong", "generic_ai", "templated", "unsupported"):
        for name, score, weak, _issue in scored[cat]:
            lines.append(f"{cat:<13} {score:>4}   {'WEAK' if weak else ' ok ':<5}  {name}")
    worst, pair = reviewer.batch_distinctiveness(REPETITIVE_BATCH)
    varied_worst, varied_pair = reviewer.batch_distinctiveness(VARIED_BATCH)
    lines.append("-" * 60)
    lines.append(f"repetitive batch: worst-similarity {worst:.2f} "
                 f"-> {'FLAGGED' if pair else 'not flagged'}")
    lines.append(f"varied batch:     worst-similarity {varied_worst:.2f} "
                 f"-> {'FLAGGED' if varied_pair else 'not flagged'}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(report())
