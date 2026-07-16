"""Batch FINGERPRINT detector for the writer overhaul.

The old detector (eval/writer_detector) checks a *small* batch for pairwise-unique
openings. That's the wrong lens at scale: across 1,000 emails, opening shapes
*must* repeat (pigeonhole) — the question isn't "are any two identical" but "is any
structural dimension too CONCENTRATED." A fingerprint is a distribution that
clusters: if 70% of emails open with a question, or a quarter share the exact same
skeleton, a human reading hundreds will feel the template even when no single email
trips a wording/structure tell.

So this harness measures the DISTRIBUTION (normalized entropy + top-share) of the
structural dimensions the redesign is meant to spread — driven now by per-prospect
reasoning, not a random menu:

    opening shape · CTA shape · greeting type · paragraph count ·
    sentence-count bucket · length bucket · full structural signature

A dimension FAILS if its normalized entropy is low OR one category dominates; the
signature FAILS if the single most common skeleton is over-represented. Thresholds
are conservative and documented below (tune with --strict).

    # OFFLINE (no network): prove the detector by scoring a deliberately DIVERSE
    # batch (passes) vs a TEMPLATED batch (fails on every dimension).
    python -m eval.writer_fingerprint --demo

    # Analyze a batch of {company, subject, body} rows — the real test. Point it at
    # a large export of your actually-generated/sent emails.
    python -m eval.writer_fingerprint --report eval/my_emails.jsonl

    # LIVE smoke test (needs ANTHROPIC_API_KEY): generate from the writer_ab
    # fixtures, then report. For a true 1,000-email verdict, run --report on a real
    # export instead — fixtures are few, so their spread understates the real one.
    python -m eval.writer_fingerprint --generate --out eval/writer_fp.jsonl

HONEST LIMITATION: this measures STRUCTURAL distribution deterministically. It is
the right signal for "recurring template," but it is not a semantic AI-content
detector — pair it with a real detector (GPTZero / Originality.ai) on the dumped
bodies for the wording/semantic axis.
"""

import argparse
import json
import math
import os
import re
import sys
from collections import Counter

os.environ.setdefault("TELEMETRY_DISABLED", "1")
os.environ.setdefault("AUTOMATION_FORCE_SQLITE", "1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agents import ai_voice  # noqa: E402

# ── Thresholds (conservative; --strict tightens) ──────────────────────────
NORM_ENTROPY_MIN = 0.55          # a dimension flatter than this is concentrated
TOP_SHARE_MAX = 0.60             # no single category may exceed this share
SIGNATURE_TOP_SHARE_MAX = 0.25   # no single full skeleton may exceed this share

_OFFER_RE = re.compile(
    r"\b(send you|send the|want (?:the|a|to see)|worth (?:sending|a)|"
    r"i'?d write|the one (?:i'?d|example)|sample|show you|poke about|"
    r"want the version)\b", re.IGNORECASE)
_GREET_HEY = re.compile(r"^\s*hey\s+([A-Z][a-z]+)\s*,", re.IGNORECASE)
_GREET_HI = re.compile(r"^\s*hi\s+([A-Z][a-z]+)\s*,", re.IGNORECASE)
_GREET_NAME = re.compile(r"^\s*([A-Z][a-z]+)\s*,")
_GREET_BARE = re.compile(r"^\s*(hey|hi|hello)\s*,", re.IGNORECASE)


# ── Structural feature extractors (deterministic, never raise) ─────────────
def greeting_type(body: str) -> str:
    head = (body or "").lstrip().split("\n", 1)[0]
    if _GREET_HEY.match(head):
        return "hey_name"
    if _GREET_HI.match(head):
        return "hi_name"
    if _GREET_BARE.match(head):
        return "bare"
    if _GREET_NAME.match(head):
        return "name_only"
    return "none"


def _first_content_sentence(body: str) -> str:
    """First sentence, with a leading greeting clause stripped so we classify the
    actual opening move, not 'Hey Jane,'."""
    sents = ai_voice.split_sentences(body or "")
    if not sents:
        return ""
    first = sents[0]
    for rx in (_GREET_HEY, _GREET_HI, _GREET_BARE, _GREET_NAME):
        m = rx.match(first)
        if m:
            return first[m.end():].strip() or (sents[1] if len(sents) > 1 else "")
    return first


def opening_shape(body: str) -> str:
    s = _first_content_sentence(body).strip()
    if not s:
        return "empty"
    low = s.lower()
    if s.endswith("?"):
        return "question"
    if re.search(r"\d", s):
        return "number_fact"
    if re.match(r"^(saw|noticed|watched|read|caught)\b", low):
        return "observation"
    if len(s.split()) <= 5:
        return "short_punch"
    if re.match(r"^(who|what|when|the|your)\b", low):
        return "framing"
    return "statement"


def cta_shape(body: str) -> str:
    sents = ai_voice.split_sentences(body or "")
    if not sents:
        return "none"
    last = sents[-1].strip()
    if last.endswith("?"):
        return "question"
    if _OFFER_RE.search(last):
        return "offer"
    if len(last.split()) <= 5:
        return "short_statement"
    return "statement"


def paragraph_count(body: str) -> int:
    return max(1, len([b for b in (body or "").split("\n\n") if b.strip()]))


def sentence_bucket(body: str) -> str:
    n = len(ai_voice.split_sentences(body or ""))
    if n <= 2:
        return "1-2"
    if n <= 4:
        return "3-4"
    if n <= 6:
        return "5-6"
    return "7+"


def length_bucket(body: str) -> str:
    n = len((body or "").split())
    for edge, label in ((40, "<40"), (60, "40-59"), (80, "60-79"), (106, "80-105")):
        if n < edge:
            return label
    return ">105"


FEATURES = {
    "opening": opening_shape,
    "cta": cta_shape,
    "greeting": greeting_type,
    "paragraphs": lambda b: str(paragraph_count(b)),
    "sentences": sentence_bucket,
    "length": length_bucket,
}


def signature(body: str) -> str:
    """A coarse whole-email skeleton: if a quarter of a batch share this exact
    tuple, that's a template even when every individual email looks fine."""
    return "|".join((opening_shape(body), cta_shape(body),
                     f"p{paragraph_count(body)}", sentence_bucket(body)))


# ── Distribution math ─────────────────────────────────────────────────────
def _norm_entropy(counts: Counter) -> float:
    total = sum(counts.values())
    if total <= 0 or len(counts) <= 1:
        return 0.0 if len(counts) <= 1 else 1.0
    h = -sum((c / total) * math.log(c / total) for c in counts.values() if c)
    return h / math.log(len(counts))


def analyze(rows: list, *, strict: bool = False) -> dict:
    ent_min = 0.65 if strict else NORM_ENTROPY_MIN
    top_max = 0.50 if strict else TOP_SHARE_MAX
    sig_max = 0.18 if strict else SIGNATURE_TOP_SHARE_MAX
    n = len(rows)
    dims = {}
    for name, fn in FEATURES.items():
        counts = Counter(fn(r.get("body", "")) for r in rows)
        top_cat, top_n = counts.most_common(1)[0]
        top_share = top_n / n if n else 0.0
        ent = _norm_entropy(counts)
        dims[name] = {
            "distribution": dict(counts.most_common()),
            "entropy": round(ent, 3),
            "top": top_cat,
            "top_share": round(top_share, 3),
            "fingerprint": bool(n >= 8 and (ent < ent_min or top_share > top_max)),
        }
    sig_counts = Counter(signature(r.get("body", "")) for r in rows)
    sig_top, sig_top_n = sig_counts.most_common(1)[0]
    sig_share = sig_top_n / n if n else 0.0
    signature_flag = bool(n >= 8 and sig_share > sig_max)
    flagged = [k for k, v in dims.items() if v["fingerprint"]]
    if signature_flag:
        flagged.append("signature")
    return {
        "n": n,
        "dimensions": dims,
        "signature": {"top": sig_top, "top_share": round(sig_share, 3),
                      "distinct": len(sig_counts), "fingerprint": signature_flag},
        "flagged": flagged,
        "verdict": "PASS" if not flagged and n >= 8 else ("FAIL" if flagged else "TOO_FEW"),
    }


# ── Reporting ─────────────────────────────────────────────────────────────
def print_report(res: dict, title: str = "fingerprint report"):
    print(f"\n=== {title} (n={res['n']}) ===")
    for name, d in res["dimensions"].items():
        flag = "  <<< FINGERPRINT" if d["fingerprint"] else ""
        dist = ", ".join(f"{k} {v}" for k, v in d["distribution"].items())
        print(f"  {name:11} entropy={d['entropy']:<5} top={d['top']}"
              f" ({int(d['top_share']*100)}%)  [{dist}]{flag}")
    sig = res["signature"]
    sflag = "  <<< FINGERPRINT" if sig["fingerprint"] else ""
    print(f"  {'signature':11} distinct={sig['distinct']} "
          f"top={sig['top']} ({int(sig['top_share']*100)}%){sflag}")
    verdict = res["verdict"]
    if verdict == "PASS":
        print("  VERDICT: PASS — no structural dimension is over-concentrated.")
    elif verdict == "TOO_FEW":
        print("  VERDICT: TOO_FEW — need >= 8 emails for a meaningful distribution.")
    else:
        print(f"  VERDICT: FAIL — clustered on: {', '.join(res['flagged'])}.")


def _load_jsonl(path: str) -> list:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


# ── Offline demo fixtures ─────────────────────────────────────────────────
# DIVERSE: eight emails that vary opening, close, greeting, length and skeleton on
# purpose (what the redesign should produce). TEMPLATED: eight emails on the SAME
# skeleton (greeting-name -> observation -> "I built" -> "Worth a look?") — the
# fingerprint the redesign kills. The demo asserts the detector PASSES the first
# and FAILS the second, so the harness itself is trustworthy without a network.
_DIVERSE = [
    "Hey Ada, saw Beacon shipped SSO last week.\n\nThe unglamorous stuff is what actually wins enterprise, and most teams bury it in a changelog. Outbound is the other place that same care tends to slip. I make first emails that read like a person did the reading. Want the one I'd send for Beacon?",
    "$1M ARR and no VC. Almost unheard of in analytics. Privacy folks smell a mass email instantly, so outreach has to be as deliberate as the product. You the right person to poke about this?",
    "Who's owning cold outreach at Figma right now? Genuine question.",
    "Eric, Ramp saving customers money is a pitch that basically sells itself. Outbound is usually where that discipline cracks and everyone just blasts the list. I built the account-by-account version of it instead. Figured the person who'd care should at least know it exists.",
    "Noticed the self-host push is landing with the exact engineers you want.\n\nThose folks can't stand a templated cold email, which makes outreach genuinely hard for a tool like yours. That happens to be the part I work on. Happy to send the PostHog version I'd write, if it's useful.",
    "Hi Sam, two GTM roles opened this month, right after the raise.\n\nIn my experience that usually means the founder is still the one running outbound and quietly feeling it, because there's nobody to hand it to yet. That's the exact stretch I built this for, so the timing felt worth a note. No pitch here. Ignore if it's not the moment.",
    "quick one on your docs. the RAG examples are unusually clear, which is genuinely rare. i do outbound that tries to hold that same bar. reply if that's useful to you.",
    "Linear for Agents was the launch I kept coming back to last week.\n\nKeyboard-first surviving the agent handoffs is the sharp part, the thing most tools would have fumbled on the way. Outbound is the one surface where that same level of care tends to vanish and everyone reverts to the blast. I'm deliberately building the opposite of that. Would it be worth me sending over the exact version I'd write for Linear, so you can judge it yourself?",
    "Hey Priya, is anyone actually happy with how cold outbound runs at your stage? Honest question. Most founders I talk to quietly hate it and do it anyway. I made a version that doesn't feel like spam to send or to get. Want a sample aimed at your market?",
    "Hi Dana, Retool made building internal tools feel almost too easy, which is a hard bar to clear.\n\nThe odd part is how much outbound still gets blasted out, even by teams that careful about their own product. I built something for the version that isn't a blast. Not selling, just think it fits what you already obviously care about.",
]
_TEMPLATED = [
    "Hey John, saw Acme raised a Series A. Congrats on the momentum. I built a tool that helps founders with outbound. Worth a look?",
    "Hey Sarah, saw Globex launched a new feature. Congrats on the momentum. I built a tool that helps founders with outbound. Worth a look?",
    "Hey Mike, saw Initech hired a VP Sales. Congrats on the momentum. I built a tool that helps founders with outbound. Worth a look?",
    "Hey Dana, saw Umbrella opened an office. Congrats on the momentum. I built a tool that helps founders with outbound. Worth a look?",
    "Hey Leo, saw Hooli shipped an update. Congrats on the momentum. I built a tool that helps founders with outbound. Worth a look?",
    "Hey Priya, saw Stark closed a round. Congrats on the momentum. I built a tool that helps founders with outbound. Worth a look?",
    "Hey Omar, saw Wayne launched a product. Congrats on the momentum. I built a tool that helps founders with outbound. Worth a look?",
    "Hey Nia, saw Cyberdyne raised funding. Congrats on the momentum. I built a tool that helps founders with outbound. Worth a look?",
]


def demo(strict: bool = False) -> int:
    diverse = analyze([{"body": b} for b in _DIVERSE], strict=strict)
    templated = analyze([{"body": b} for b in _TEMPLATED], strict=strict)
    print_report(diverse, "DIVERSE batch (should PASS)")
    print_report(templated, "TEMPLATED batch (should FAIL)")
    ok = diverse["verdict"] == "PASS" and templated["verdict"] == "FAIL"
    print("\nDEMO SELF-CHECK:", "OK" if ok else "UNEXPECTED",
          "— detector passes diverse and flags templated." if ok else
          "— thresholds may need tuning.")
    return 0 if ok else 1


def generate(out: str, limit: int = 0) -> int:
    """Live: generate one email per writer_ab fixture, then report (needs key)."""
    from eval.writer_ab import FIXTURES, _fixture_data
    from agents.writer import write_email
    fixtures = FIXTURES[:limit] if limit else FIXTURES
    rows = []
    for company, first, role, hook, icp in fixtures:
        res = write_email(_fixture_data(company, first, role, hook, icp))
        if res.get("status") == "ok":
            rows.append({"company": company, "subject": res.get("subject", ""),
                         "body": res.get("body", "")})
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        with open(os.path.splitext(out)[0] + ".txt", "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(r["body"].replace("\n", " ") + "\n")
        print(f"wrote {len(rows)} rows -> {out}")
    print_report(analyze(rows), "generated batch")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Batch structural fingerprint detector.")
    ap.add_argument("--demo", action="store_true", help="offline diverse-vs-templated proof")
    ap.add_argument("--report", metavar="JSONL", help="analyze a {company,subject,body} batch")
    ap.add_argument("--score", metavar="JSONL", help="alias for --report")
    ap.add_argument("--generate", action="store_true", help="live-generate from fixtures + report")
    ap.add_argument("--out", default="eval/writer_fp.jsonl", help="output for --generate")
    ap.add_argument("--limit", type=int, default=0, help="cap fixtures for --generate")
    ap.add_argument("--strict", action="store_true", help="tighter fingerprint thresholds")
    args = ap.parse_args()

    if args.demo:
        return demo(strict=args.strict)
    if args.generate:
        return generate(args.out, limit=args.limit)
    path = args.report or args.score
    if path:
        print_report(analyze(_load_jsonl(path), strict=args.strict), os.path.basename(path))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
