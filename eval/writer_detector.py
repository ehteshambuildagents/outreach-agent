"""AI-voice detector harness for the writer overhaul.

The overhaul's definition of done is a batch of writer output that (1) scores low
on an AI-content detector, (2) shows no two emails sharing an opening structure,
and (3) anchors on something specific to each prospect. This harness measures all
three — plus the guard's own verdict on the batch — so the fix can be verified,
not just asserted.

    # OFFLINE (no network / no key): score built-in good vs generic fixtures and
    # show the guard blocking the generic ones. Proves detector + guard end-to-end.
    python -m eval.writer_detector --demo

    # OFFLINE: score an existing batch of {company,subject,body} rows.
    python -m eval.writer_detector --score eval/writer_new.jsonl

    # LIVE (needs ANTHROPIC_API_KEY): generate one email per fixture company with
    # the real writer, score the batch, and dump bodies for an external detector.
    python -m eval.writer_detector --generate --out eval/writer_detect.jsonl

    # Summarize any generated/scored jsonl.
    python -m eval.writer_detector --report eval/writer_detect.jsonl

HONEST LIMITATION: a real third-party detector (GPTZero, Originality.ai, ...)
needs the network and an account, so it CANNOT run inside the tool sandbox. The
`ai_score` here is a transparent, deterministic PROXY built from the very tells we
ban (see agents/ai_voice.py) — good for regression-tracking, not a substitute for
the real thing. `--generate`/`--demo` also write the raw bodies to a sibling
`.txt`, one per line, so a human can paste them into a real detector in one step.
"""

import argparse
import difflib
import json
import os
import re
import statistics
import sys

os.environ.setdefault("TELEMETRY_DISABLED", "1")
os.environ.setdefault("AUTOMATION_FORCE_SQLITE", "1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agents import ai_voice  # noqa: E402
from agents.writer_validator import find_banned  # noqa: E402
from guard import assess as guard_assess  # noqa: E402

# ── Offline fixtures: 5 GOOD founder emails (deliberately different openings) and
#    3 GENERIC AI emails. Used by --demo so the harness proves the detector AND
#    the guard with zero network. The good ones are grounded, human, and each
#    opens a different way (question / observation / statement / name-only / cold
#    fact); the generic ones are the swappable-template copy we're killing.
_GOOD = [
    ("Plausible", "the bootstrapped number",      # opening shape: cold-fact statement
     "$1M ARR and no VC. In analytics that's almost unheard of. Privacy folks can "
     "smell a mass email instantly, so outreach has to be as deliberate as the "
     "product. I built a thing for those account-by-account notes so they don't "
     "eat your afternoon. You the right person to poke about outbound?"),
    ("Linear", "triage",                           # opening shape: observation
     "Saw Linear for Agents shipped last week. The keyboard-first flow surviving "
     "the agent handoffs is the sharp part. Outbound's the one spot most tools let "
     "get sloppy. I'm building the opposite. Want the version I'd write for you?"),
    ("Figma", "multiplayer",                       # opening shape: question
     "Who's thinking about Figma's cold outreach these days? Genuine question. The "
     "multiplayer thing earned attention by being unmistakably yours, and a first "
     "email has to do the same. That's the part I work on. Send you a sample?"),
    ("Ramp", "the spend angle",                    # opening shape: name-only
     "Eric, Ramp saving customers money is a pitch that sells itself. Outbound's "
     "where that discipline usually cracks, everyone just blasts. I made something "
     "for the account-by-account version instead. That a problem you're feeling?"),
    ("PostHog", "self-host",                        # opening shape: greeting + name
     "Hi James, self-hostable analytics is a real wedge with engineers, and they "
     "can't stand a templated cold email. So I make outreach that reads like a "
     "person actually did the reading. Worth sending you the PostHog one?"),
]
_GENERIC = [
    ("Acme", "Partnership Opportunity",
     "Dear Sir, I hope this email finds you well. I wanted to reach out because our "
     "cutting-edge solution can leverage synergies to unlock seamless growth. It's "
     "not just a tool, but a partner, ensuring lasting value. Looking forward to "
     "connecting."),
    ("Globex", "Boost Your Revenue",
     "I came across your company and was impressed. Our platform delivers speed, "
     "scale, and precision, helping teams supercharge results and unlock their full "
     "potential. That said, I'd love to hear your thoughts. No worries if not."),
    ("Initech", "Quick Question",
     "I noticed that your business could benefit from our game-changing solution. "
     "We help companies transform, streamline, and grow, driving real impact. With "
     "that in mind, I wanted to touch base and circle back on next steps."),
]


def _row(company, subject, body):
    """Score one email into a flat, reportable row."""
    banned = find_banned(subject + "\n" + body, [company])
    tells = ai_voice.tells(body)
    guard = guard_assess({"email": {"subject": subject, "body": body,
                                    "to": "someone@example.com", "company": company}})
    return {
        "company": company,
        "subject": subject,
        "body": body,
        "ai_score": ai_voice.ai_score(body),
        "banned": banned,
        "tells": tells,
        "opening": _opening_signature(body),
        "words": len(body.split()),
        "anchored": _anchored(company, body),
        "guard": guard["decision"],
        "guard_risk": guard["overallRisk"],
    }


def _opening_signature(body: str) -> str:
    """Classify the OPENING structure so we can check no two share one (DoD #2)."""
    sents = ai_voice.split_sentences(body)
    first = (sents[0] if sents else "").strip()
    low = first.lower()
    if first.endswith("?"):
        shape = "question"
    elif re.match(r"^(hi|hey|hello)\s+[a-z]+,", low):
        shape = "greeting-name"
    elif re.match(r"^[a-z]+,", low):
        shape = "name-only"
    elif low.startswith(("saw ", "caught ", "noticed ", "just saw")):
        shape = "observation"
    else:
        shape = "statement"
    # first 3 content words, to catch same-wording openers the shape misses
    words = "-".join(re.findall(r"[a-z0-9']+", low)[:3])
    return f"{shape}:{words}"


def _anchored(company: str, body: str) -> bool:
    """Does the email reference something specific enough it couldn't be swapped
    into another company's email unchanged? (Company name or a capitalised
    proper noun beyond the greeting is a cheap, honest proxy.)"""
    low = body.lower()
    if company and company.lower() in low:
        return True
    # A distinctive capitalised token that isn't the leading greeting word.
    caps = re.findall(r"(?<![.!?]\s)(?<!^)\b([A-Z][A-Za-z0-9]{2,})\b", body)
    return len([c for c in caps if c.lower() not in ("hi", "hey", "hello")]) > 0


def _pairwise_similarity(bodies):
    if len(bodies) < 2:
        return 0.0
    tot = cnt = 0
    for i in range(len(bodies)):
        for j in range(i + 1, len(bodies)):
            tot += difflib.SequenceMatcher(
                a=bodies[i].lower(), b=bodies[j].lower()).ratio()
            cnt += 1
    return round(tot / cnt, 4)


def _summarize(rows: list) -> dict:
    n = len(rows) or 1
    scores = [r["ai_score"] for r in rows]
    openings = [r["opening"] for r in rows]
    return {
        "n": len(rows),
        "ai_score_mean": round(statistics.mean(scores), 1),
        "ai_score_median": round(statistics.median(scores), 1),
        "ai_score_max": max(scores),
        "pct_over_10": round(100 * sum(1 for s in scores if s > 10) / n, 1),
        "pct_banned": round(100 * sum(1 for r in rows if r["banned"]) / n, 1),
        "pct_structural_tell": round(100 * sum(1 for r in rows if r["tells"]) / n, 1),
        "distinct_openings": len(set(openings)),
        "pct_anchored": round(100 * sum(1 for r in rows if r["anchored"]) / n, 1),
        "avg_pairwise_similarity": _pairwise_similarity([r["body"] for r in rows]),
        "guard": {d: sum(1 for r in rows if r["guard"] == d)
                  for d in ("ALLOW", "WARN", "BLOCK")},
    }


def _print_table(rows: list, title: str):
    print(f"\n{title}")
    print(f"{'company':12} {'score':>5} {'guard':>6} {'anchor':>7}  opening / tells")
    print("-" * 78)
    for r in rows:
        flags = r["opening"].split(":")[0]
        if r["banned"]:
            flags += " +banned"
        if r["tells"]:
            flags += f" +{len(r['tells'])}tell"
        print(f"{r['company'][:12]:12} {r['ai_score']:>5} {r['guard']:>6} "
              f"{('yes' if r['anchored'] else 'NO'):>7}  {flags}")


def _print_summary(summary: dict):
    print("\nSUMMARY")
    for k in ("n", "ai_score_mean", "ai_score_median", "ai_score_max", "pct_over_10",
              "pct_banned", "pct_structural_tell", "distinct_openings",
              "pct_anchored", "avg_pairwise_similarity"):
        print(f"  {k:24} {summary[k]}")
    print(f"  {'guard verdicts':24} {summary['guard']}")


def _dump_bodies(rows: list, path: str):
    """Write raw bodies (one per line) for pasting into a real external detector."""
    txt = os.path.splitext(path)[0] + ".bodies.txt"
    with open(txt, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(re.sub(r"\s+", " ", r["body"]).strip() + "\n\n")
    print(f"\nWrote raw bodies to {txt}")
    print("NOTE: ai_score is a local PROXY, not a real detector. Paste the bodies "
          "above into GPTZero / Originality.ai to confirm the real score.")


def demo():
    good = [_row(c, s, b) for c, s, b in _GOOD]
    generic = [_row(c, s, b) for c, s, b in _GENERIC]
    _print_table(good, "GOOD (human, grounded, varied openings)")
    _print_summary(_summarize(good))
    _print_table(generic, "GENERIC (swappable AI template copy)")
    _print_summary(_summarize(generic))
    print("\nEXPECTED: good -> low score, distinct openings, guard ALLOW/WARN; "
          "generic -> high score, guard BLOCK.")
    all_rows = good + generic
    _dump_bodies(all_rows, os.path.join(ROOT, "eval", "writer_detector_demo.jsonl"))


def score_file(path: str):
    rows = []
    for line in open(path, encoding="utf-8"):
        if not line.strip():
            continue
        d = json.loads(line)
        rows.append(_row(d.get("company") or "?", d.get("subject") or "",
                         d.get("body") or ""))
    _print_table(rows, f"SCORED {path}")
    _print_summary(_summarize(rows))


def report(path: str):
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    # Rows written by --generate already carry scores; re-derive to be safe.
    rescored = [_row(r.get("company") or "?", r.get("subject") or "",
                     r.get("body") or "") for r in rows]
    _print_summary(_summarize(rescored))


def generate(out: str, limit: int = 0):
    """Live: generate one email per fixture company and score the batch."""
    from eval.writer_ab import FIXTURES, _fixture_data
    from agents.writer import write_email

    fixtures = FIXTURES[:limit] if limit else FIXTURES
    rows = []
    with open(out, "w", encoding="utf-8") as fh:
        for company, first, role, hook, icp in fixtures:
            res = write_email(_fixture_data(company, first, role, hook, icp))
            if res.get("status") != "ok":
                print(f"  {company}: {res.get('status')} "
                      f"({str(res.get('reason', ''))[:40]})", flush=True)
                continue
            row = _row(company, res["subject"], res["body"])
            rows.append(row)
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            print(f"  {company:12} score={row['ai_score']:>3} guard={row['guard']} "
                  f"{row['opening'].split(':')[0]}", flush=True)
    if rows:
        _print_summary(_summarize(rows))
        _dump_bodies(rows, out)
        print(f"\nWrote {out}")


def main():
    ap = argparse.ArgumentParser(description="AI-voice detector harness for the writer.")
    ap.add_argument("--demo", action="store_true", help="offline good vs generic fixtures")
    ap.add_argument("--score", metavar="JSONL", help="score bodies in a jsonl file")
    ap.add_argument("--report", metavar="JSONL", help="summarize a generated jsonl")
    ap.add_argument("--generate", action="store_true", help="live-generate + score (needs API key)")
    ap.add_argument("--limit", type=int, default=0, help="cap fixtures for --generate (0 = all)")
    ap.add_argument("--out", default=os.path.join("eval", "writer_detect.jsonl"))
    args = ap.parse_args()

    if args.score:
        score_file(args.score)
    elif args.report:
        report(args.report)
    elif args.generate:
        generate(args.out, limit=args.limit)
    else:
        demo()          # default: fully offline proof


if __name__ == "__main__":
    main()
