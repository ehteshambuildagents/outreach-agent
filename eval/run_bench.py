"""Instrumented benchmark — extends the eval harness to measure REAL cost + tokens.

The base harness (run_eval.py) records pages/latency/score but NOT cost/tokens.
This runner reuses ``research_company`` unchanged and captures per-company cost and
token totals from the telemetry system (each company is wrapped in a telemetry
``scope`` so ``ai_requests`` rows are attributable by campaign_id=url). Nothing
about the research agent changes; this only MEASURES.

Deterministic subset: the first N companies of eval.companies (default 30), so the
run is reproducible and comparable across versions. Resumable via a fresh JSONL.

    python -m eval.run_bench --limit 30 --out eval/results_v4.jsonl
    python -m eval.run_bench --report --out eval/results_v4.jsonl
"""

import json
import os
import sys
import time

from config.env import load_env

load_env()

import telemetry  # noqa: E402
from telemetry import query  # noqa: E402
from agents.research import research_company  # noqa: E402
from eval.companies import COMPANIES  # noqa: E402

_SENIOR = ("founder", "co-founder", "cofounder", "ceo", "cto", "coo", "cfo", "cmo",
           "chief", "president", "vp ", "vice president", "head of", "director",
           "owner", "partner", "managing")


def _person_metrics(data: dict) -> dict:
    """founder / decision-maker / named-contact flags from the research data."""
    founder = bool(data.get("founder_name"))
    names, roles = [], []
    if data.get("founder_name"):
        names.append(data["founder_name"]); roles.append(data.get("founder_title") or "founder")
    if data.get("primary_contact_name"):
        names.append(data["primary_contact_name"]); roles.append(data.get("primary_contact_role") or "")
    for m in (data.get("team_members") or []):
        if isinstance(m, dict) and m.get("name"):
            names.append(m["name"]); roles.append(m.get("role") or "")
    named = bool(names)
    decision_maker = founder or any(
        any(k in (r or "").lower() for k in _SENIOR) for r in roles)
    return {"founder": founder, "decision_maker": decision_maker, "named_contact": named}


def _completeness(data: dict) -> int:
    signals = ("what_they_do", "target_customer", "recent_focus", "notable_customers",
               "product_category", "business_model", "company_stage", "industries_served")
    return round(100 * sum(1 for f in signals if data.get(f)) / len(signals))


def _load_done(path):
    done = set()
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            try:
                done.add(json.loads(line)["url"])
            except Exception:
                pass
    return done


def run(limit, out):
    subset = COMPANIES[:limit]
    done = _load_done(out)
    todo = [(n, u, i) for (n, u, i) in subset if u not in done]
    print(f"{len(done)} done; running {len(todo)} of {len(subset)} -> {out}\n", flush=True)
    with open(out, "a", encoding="utf-8") as fh:
        for idx, (name, url, industry) in enumerate(todo, 1):
            print(f"[{idx}/{len(todo)}] {name} ...", end="", flush=True)
            with telemetry.scope(campaign_id=url, user_id="bench"):
                t = time.perf_counter()
                try:
                    r = research_company(url)
                except Exception as exc:
                    r = {"status": "error", "error": f"harness: {exc}"}
                secs = round(time.perf_counter() - t, 1)
            telemetry.flush()
            db = query._db()
            cost = query.campaign_cost(url, db=db)
            toks = int(query._scalar(
                db, "SELECT COALESCE(SUM(total_tokens),0) FROM ai_requests WHERE campaign_id=?", (url,)))
            calls = int(query._scalar(
                db, "SELECT COUNT(*) FROM ai_requests WHERE campaign_id=?", (url,)))
            data = r.get("data") or {}
            pm = _person_metrics(data)
            row = {
                "name": name, "url": url, "industry": industry,
                "status": r.get("status"), "pages": len(r.get("pages_crawled") or []),
                "seconds": secs, "cost": round(cost, 6), "tokens": toks, "llm_calls": calls,
                "score": r.get("research_score", 0),
                "completeness": _completeness(data) if r.get("status") == "ok" else 0,
                "founder": pm["founder"], "decision_maker": pm["decision_maker"],
                "named_contact": pm["named_contact"],
                "homepage_only": len(r.get("pages_crawled") or []) <= 1,
                "stop_reason": r.get("stop_reason"),
            }
            fh.write(json.dumps(row) + "\n"); fh.flush()
            print(f" {row['status']} pages={row['pages']} {secs}s ${cost:.4f} "
                  f"tok={toks} score={row['score']} founder={pm['founder']} "
                  f"dm={pm['decision_maker']}", flush=True)


def report(out):
    rows = [json.loads(l) for l in open(out, encoding="utf-8") if l.strip()]
    n = len(rows)
    if not n:
        print("no rows"); return
    ok = [r for r in rows if r["status"] == "ok"]
    import statistics as st

    def pct(key):
        return 100 * sum(1 for r in rows if r.get(key)) / n

    print(f"\n===== BENCHMARK (n={n}) =====")
    print(f"avg pages         : {st.mean(r['pages'] for r in rows):.2f}")
    print(f"homepage-only %   : {pct('homepage_only'):.1f}%")
    print(f"founder discovery : {pct('founder'):.1f}%")
    print(f"decision-maker    : {pct('decision_maker'):.1f}%")
    print(f"named contact     : {pct('named_contact'):.1f}%")
    print(f"research score    : {st.mean(r['score'] for r in ok):.1f}" if ok else "n/a")
    print(f"completeness      : {st.mean(r['completeness'] for r in ok):.1f}" if ok else "n/a")
    print(f"avg cost          : ${st.mean(r['cost'] for r in rows):.4f}")
    print(f"avg latency       : {st.mean(r['seconds'] for r in rows):.1f}s")
    print(f"avg tokens        : {st.mean(r['tokens'] for r in rows):.0f}")
    print(f"avg llm calls     : {st.mean(r['llm_calls'] for r in rows):.1f}")
    print(f"total cost        : ${sum(r['cost'] for r in rows):.2f}")
    fp = [r for r in rows if r.get('named_contact')]
    nfp = [r for r in rows if not r.get('named_contact')]
    if fp:
        print(f"  found-person  : n={len(fp)} pages={st.mean(r['pages'] for r in fp):.1f} "
              f"${st.mean(r['cost'] for r in fp):.4f} {st.mean(r['seconds'] for r in fp):.0f}s")
    if nfp:
        print(f"  no-person     : n={len(nfp)} pages={st.mean(r['pages'] for r in nfp):.1f} "
              f"${st.mean(r['cost'] for r in nfp):.4f} {st.mean(r['seconds'] for r in nfp):.0f}s")


def main():
    args = sys.argv[1:]
    out = "eval/results_v4.jsonl"
    if "--out" in args:
        out = args[args.index("--out") + 1]
    if "--report" in args:
        report(out); return
    limit = 30
    if "--limit" in args:
        limit = int(args[args.index("--limit") + 1])
    run(limit, out)
    report(out)


if __name__ == "__main__":
    main()
