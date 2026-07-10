"""Writer A/B benchmark — structural diversity of OLD vs NEW writer.

Generates one cold email per company from GROUNDED research fixtures (real
companies, public one-line facts — no invented metrics), so the measurement
isolates the WRITER's structure, not research quality. Records per-email
structural signals and reports diversity/repetition metrics.

    python -m eval.writer_ab --out eval/writer_new.jsonl     # generate with current code
    # then: git stash push -- agents/writer_prompt.py
    python -m eval.writer_ab --out eval/writer_old.jsonl     # generate with old writer
    # then: git stash pop
    python -m eval.writer_ab --report eval/writer_old.jsonl eval/writer_new.jsonl

Never sends anything; telemetry is disabled for the run.
"""

import difflib
import json
import os
import re
import sys

os.environ.setdefault("TELEMETRY_DISABLED", "1")
os.environ.setdefault("AUTOMATION_FORCE_SQLITE", "1")

from config.env import load_env  # noqa: E402

load_env()

from agents.writer import write_email  # noqa: E402
from agents.writer_validator import find_banned  # noqa: E402

# Grounded fixtures: (company, contact_first, role, TRUE public one-line hook, ICP).
# Facts are public/category-level (business model, positioning), never invented
# numbers. Some deliberately have no contact name (tests the nameless path).
FIXTURES = [
    ("Plausible", "Uku", "Founder", "privacy-first analytics, bootstrapped, no VC", "founders"),
    ("Linear", "Karri", "CEO", "issue tracking built for high-velocity software teams", "eng teams"),
    ("Notion", None, None, "all-in-one workspace replacing scattered docs and wikis", "teams"),
    ("Figma", None, None, "browser-based collaborative design, multiplayer editing", "designers"),
    ("Vercel", "Guillermo", "CEO", "frontend cloud for shipping fast on the edge", "frontend devs"),
    ("Supabase", "Paul", "CEO", "open-source Firebase alternative on Postgres", "developers"),
    ("Retool", None, None, "build internal tools fast from a drag-and-drop builder", "eng teams"),
    ("Ramp", "Eric", "CEO", "corporate cards and spend management that saves money", "finance teams"),
    ("Deel", None, None, "global payroll and hiring for remote teams", "HR teams"),
    ("Airtable", None, None, "spreadsheet-database hybrid for building apps without code", "ops teams"),
    ("Webflow", None, None, "visual website builder, design without hand-coding", "marketers"),
    ("Calendly", None, None, "scheduling that removes the back-and-forth email", "sales teams"),
    ("Loom", None, None, "async video messaging instead of another meeting", "distributed teams"),
    ("Miro", None, None, "online whiteboard for distributed collaboration", "product teams"),
    ("Segment", None, None, "customer-data pipeline that unifies analytics tooling", "data teams"),
    ("Amplitude", None, None, "product analytics for understanding user behavior", "product teams"),
    ("Twilio", None, None, "programmable messaging and voice APIs for developers", "developers"),
    ("Stripe", "Patrick", "CEO", "payments infrastructure for internet businesses", "developers"),
    ("Brex", None, None, "financial stack built for startups", "startup founders"),
    ("Mercury", "Immad", "CEO", "banking built for startups and founders", "founders"),
    ("Rippling", None, None, "unifies HR, IT, and finance in one system", "ops leaders"),
    ("Gusto", None, None, "payroll and benefits for small businesses", "SMB owners"),
    ("Front", None, None, "shared inbox so teams handle email together", "support teams"),
    ("Intercom", None, None, "customer messaging and support in one place", "support teams"),
    ("Zapier", "Wade", "CEO", "no-code automation connecting thousands of apps", "ops teams"),
    ("ClickUp", None, None, "one app to replace scattered project tools", "project teams"),
    ("Superhuman", "Rahul", "CEO", "the fastest email experience, keyboard-first", "execs"),
    ("Census", None, None, "reverse-ETL syncing the warehouse into business tools", "data teams"),
    ("Metabase", None, None, "open-source BI anyone on the team can query", "data teams"),
    ("PostHog", "James", "CEO", "open-source product analytics you can self-host", "engineers"),
]


def _fixture_data(company, first, role, hook, icp):
    return {
        "company_name": company, "primary_contact_name": (
            f"{first} Founder" if first else None),
        "primary_contact_role": role, "unique_hook": hook,
        "target_customer": icp, "has_enough_detail": True,
    }


def _signature(body: str) -> dict:
    sents = [s for s in re.split(r"(?<=[.!?])\s+", body.strip()) if s.strip()]
    first = sents[0] if sents else ""
    last = sents[-1] if sents else ""
    low = first.lower()
    if low.startswith("hey "):
        greet = "Hey"
    elif low.startswith("hi "):
        greet = "Hi"
    elif low.startswith("hello"):
        greet = "Hello"
    elif re.match(r"[A-Z][a-z]+,", first):
        greet = "name-only"
    else:
        greet = "dive-in"
    banned_closers = ("right person", "open to seeing", "would you be interested",
                      "happy to send")
    return {
        "greeting": greet,
        "opening4": " ".join(re.findall(r"[a-z0-9']+", low))[:40],
        "ends_question": body.rstrip().endswith("?"),
        "last_norm": re.sub(r"[^a-z0-9 ]", "", last.lower())[:60],
        "banned_closer": any(b in body.lower() for b in banned_closers),
        "words": len(body.split()),
        "sentences": len(sents),
        "paragraphs": body.count("\n\n") + 1,
    }


def run(out):
    done = set()
    if os.path.exists(out):
        done = {json.loads(l)["company"] for l in open(out) if l.strip()}
    with open(out, "a", encoding="utf-8") as fh:
        for company, first, role, hook, icp in FIXTURES:
            if company in done:
                continue
            data = _fixture_data(company, first, role, hook, icp)
            res = write_email(data)
            if res.get("status") != "ok":
                print(f"  {company}: {res.get('status')} ({res.get('reason','')[:40]})", flush=True)
                continue
            body = res["body"]
            row = {"company": company, "subject": res["subject"], "body": body,
                   "banned": find_banned(res["subject"] + "\n" + body, [company]),
                   **_signature(body)}
            fh.write(json.dumps(row) + "\n"); fh.flush()
            print(f"  {company}: {row['greeting']:9} q={row['ends_question']} "
                  f"w={row['words']} p={row['paragraphs']}", flush=True)


def _pairwise_similarity(bodies):
    if len(bodies) < 2:
        return 0.0
    tot = cnt = 0
    for i in range(len(bodies)):
        for j in range(i + 1, len(bodies)):
            tot += difflib.SequenceMatcher(a=bodies[i].lower(), b=bodies[j].lower()).ratio()
            cnt += 1
    return round(tot / cnt, 4)


def _summ(path):
    rows = [json.loads(l) for l in open(path) if l.strip()]
    n = len(rows)
    import statistics as st
    from collections import Counter
    greet = Counter(r["greeting"] for r in rows)
    return {
        "n": n,
        "greet_hey_pct": round(100 * greet.get("Hey", 0) / n, 1),
        "greet_distinct": len(greet),
        "greet_dist": dict(greet),
        "ends_q_pct": round(100 * sum(r["ends_question"] for r in rows) / n, 1),
        "banned_closer_pct": round(100 * sum(r["banned_closer"] for r in rows) / n, 1),
        "unique_openings_pct": round(100 * len({r["opening4"] for r in rows}) / n, 1),
        "unique_closings_pct": round(100 * len({r["last_norm"] for r in rows}) / n, 1),
        "avg_words": round(st.mean(r["words"] for r in rows), 1),
        "avg_sentences": round(st.mean(r["sentences"] for r in rows), 1),
        "para_dist": dict(Counter(r["paragraphs"] for r in rows)),
        "banned_rate_pct": round(100 * sum(1 for r in rows if r["banned"]) / n, 1),
        "avg_pairwise_similarity": _pairwise_similarity([r["body"] for r in rows]),
    }


def report(old_path, new_path):
    o, nw = _summ(old_path), _summ(new_path)
    keys = [("n", "emails"), ("greet_hey_pct", "% opening 'Hey'"),
            ("greet_distinct", "distinct greeting types"),
            ("ends_q_pct", "% ending with a question"),
            ("banned_closer_pct", "% using a banned closer"),
            ("unique_openings_pct", "unique openings %"),
            ("unique_closings_pct", "unique closings %"),
            ("avg_words", "avg words"), ("avg_sentences", "avg sentences"),
            ("banned_rate_pct", "banned-phrase rate %"),
            ("avg_pairwise_similarity", "avg pairwise similarity (lower=better)")]
    print(f"\n{'metric':42} {'OLD':>10} {'NEW':>10}")
    print("-" * 64)
    for k, label in keys:
        print(f"{label:42} {str(o[k]):>10} {str(nw[k]):>10}")
    print(f"\nOLD greeting dist: {o['greet_dist']}")
    print(f"NEW greeting dist: {nw['greet_dist']}")
    print(f"OLD paragraph dist: {o['para_dist']}")
    print(f"NEW paragraph dist: {nw['para_dist']}")


def main():
    a = sys.argv[1:]
    if "--report" in a:
        i = a.index("--report")
        report(a[i + 1], a[i + 2]); return
    out = a[a.index("--out") + 1] if "--out" in a else "eval/writer_new.jsonl"
    run(out)


if __name__ == "__main__":
    main()
