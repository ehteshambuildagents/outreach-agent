"""Reliability evaluation harness.

Runs the REAL research agent against eval/companies.py, records precise
per-company metrics, classifies every failure by ROOT CAUSE (not just
"failed"), and produces an honest report + a production-readiness verdict.

Design:
  * Resumable — every completed company is appended to eval/results.jsonl, so a
    long run can be interrupted and resumed (already-done URLs are skipped).
  * Never let one company crash the batch.
  * Attribution — each outcome is tagged agent / site / environment / thin so we
    can separate TRUE agent-reliability bugs from site- or sandbox-caused noise.

Usage (from project root):
    python -m eval.run_eval               # run all (resume), then report
    python -m eval.run_eval --limit 15    # run the next 15 not-yet-done
    python -m eval.run_eval --no-email    # skip email-generation step
    python -m eval.run_eval --report      # regenerate the report from checkpoint
"""

import json
import os
import sys
import time
from collections import Counter, defaultdict
from urllib.parse import urlparse

from agents.research import research_company
from eval.companies import COMPANIES

_HERE = os.path.dirname(os.path.abspath(__file__))
CHECKPOINT = os.path.join(_HERE, "results.jsonl")
REPORT = os.path.join(_HERE, "report.md")


# ──────────────────────────────────────────────────────────────────────
#  Classification — precise root causes
# ──────────────────────────────────────────────────────────────────────
def classify(result: dict) -> str:
    status = result.get("status")
    if status == "ok":
        return "success"
    if status == "skip":
        reason = (result.get("reason") or "").lower()
        if "too little readable text" in reason:
            return "empty_or_thin"
        if "research score" in reason or "below" in reason:
            return "low_confidence"
        return "skip_other"

    err = (result.get("error") or "").lower()
    checks = [
        ("timed out", "timeout"),
        ("could not resolve", "dns_failure"),
        ("could not connect", "connection_failure"),
        ("http 403", "bot_protection"), ("http 401", "bot_protection"),
        ("http 429", "bot_protection"),
        ("http 503", "server_unavailable"), ("http 502", "server_unavailable"),
        ("http 504", "server_unavailable"),
        ("http 5", "server_error"),
        ("http 4", "http_client_error"),
        ("not a text/html", "non_html"),
        ("redirect", "redirect_failure"),
        ("internal/non-public", "invalid_url"), ("url must start", "invalid_url"),
        ("no host", "invalid_url"),
        ("valid json", "json_failure"), ("empty response", "json_failure"),
        ("rate limited", "llm_rate_limit"),
        ("authentication failed", "llm_auth"), ("anthropic_api_key", "llm_auth"),
        ("anthropic api", "llm_api_failure"), ("reach the anthropic", "llm_api_failure"),
        ("unexpected error during analysis", "extraction_crash"),
        ("could not be fetched", "fetch_failure"),
    ]
    for needle, category in checks:
        if needle in err:
            return category
    return "unknown"


# category -> who is responsible. Only "agent" counts against the agent's own
# reliability; "thin" is excluded (no public info); site/environment are outside
# the agent's control (and inflated by this rate-limited sandbox).
ATTRIBUTION = {
    "success": "success",
    "empty_or_thin": "thin", "low_confidence": "thin", "skip_other": "thin",
    "timeout": "environment", "dns_failure": "environment",
    "connection_failure": "environment", "fetch_failure": "environment",
    "llm_rate_limit": "environment", "llm_auth": "environment",
    "llm_api_failure": "environment",
    "bot_protection": "site", "server_unavailable": "site", "server_error": "site",
    "http_client_error": "site", "non_html": "site", "redirect_failure": "site",
    "invalid_url": "site",
    "json_failure": "agent", "extraction_crash": "agent", "unknown": "agent",
}


def estimate_quality(result: dict) -> int:
    """Rough 0-100 research-quality estimate for a successful result."""
    if result.get("status") != "ok":
        return 0
    data = result.get("data") or {}
    score = result.get("research_score", 0)
    signals = ("what_they_do", "target_customer", "recent_focus",
               "notable_customers", "pricing_model", "product_category",
               "business_model", "company_stage", "their_mission_or_why",
               "tech_stack", "industries_served")
    filled = sum(1 for f in signals if data.get(f))
    has_person = bool(data.get("founder_name") or data.get("team_members"))
    hooks = len(result.get("hooks") or [])
    quality = (0.5 * score + 30 * (filled / len(signals))
               + (10 if has_person else 0) + min(10, hooks * 2))
    return round(min(100, quality))


# ──────────────────────────────────────────────────────────────────────
#  Runner (resumable)
# ──────────────────────────────────────────────────────────────────────
def _load_done() -> set:
    done = set()
    if os.path.exists(CHECKPOINT):
        with open(CHECKPOINT, encoding="utf-8") as fh:
            for line in fh:
                try:
                    done.add(json.loads(line)["url"])
                except Exception:
                    continue
    return done


def _evaluate_one(name, url, industry, do_email) -> dict:
    start = time.perf_counter()
    try:
        result = research_company(url)
    except Exception as exc:                       # agent must never crash us
        result = {"status": "error", "error": f"harness caught: {exc}"}
    seconds = round(time.perf_counter() - start, 1)

    category = classify(result)
    data = result.get("data") or {}
    email_status = "not_attempted"
    if do_email and result.get("status") == "ok":
        try:
            from agents.writer import write_email
            email = write_email(result)
            email_status = "ok" if email.get("status") == "ok" else \
                f"skip/{email.get('status')}"
        except Exception as exc:
            email_status = f"error:{type(exc).__name__}"

    return {
        "name": name, "url": url, "industry": industry,
        "status": result.get("status"),
        "category": category,
        "attribution": ATTRIBUTION.get(category, "agent"),
        "seconds": seconds,
        "pages": len(result.get("pages_crawled") or []),
        "pages_list": result.get("pages_crawled") or [],
        "fetch_method": result.get("fetch_method"),
        "score": result.get("research_score", 0),
        "quality": estimate_quality(result),
        "company_name": data.get("company_name"),
        "found_person": bool(data.get("founder_name") or data.get("team_members")),
        "email_status": email_status,
        "reason": result.get("reason") or result.get("error"),
    }


def _interleave(items):
    """Round-robin by industry so any prefix is a cross-industry sample."""
    buckets = defaultdict(list)
    for it in items:
        buckets[it[2]].append(it)
    out, industries = [], list(buckets)
    while any(buckets[k] for k in industries):
        for k in industries:
            if buckets[k]:
                out.append(buckets[k].pop(0))
    return out


def run(limit=None, do_email=True):
    done = _load_done()
    todo = _interleave([(n, u, i) for (n, u, i) in COMPANIES if u not in done])
    if limit:
        todo = todo[:limit]
    print(f"{len(done)} already done; running {len(todo)} now "
          f"({len(COMPANIES)} total)\n")

    with open(CHECKPOINT, "a", encoding="utf-8") as fh:
        for idx, (name, url, industry) in enumerate(todo, start=1):
            print(f"[{idx:>3}/{len(todo)}] {name} ({industry}) ...",
                  end="", flush=True)
            row = _evaluate_one(name, url, industry, do_email)
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            print(f" {row['status']}/{row['category']}  "
                  f"score={row['score']} {row['seconds']}s")


# ──────────────────────────────────────────────────────────────────────
#  Report
# ──────────────────────────────────────────────────────────────────────
def _read_rows() -> list:
    rows = []
    if os.path.exists(CHECKPOINT):
        with open(CHECKPOINT, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    return rows


def _page_section(url: str) -> str:
    seg = urlparse(url).path.strip("/").split("/", 1)[0].lower()
    return seg or "(home)"


def generate_report() -> str:
    rows = _read_rows()
    n = len(rows)
    if n == 0:
        return "No results yet."

    cat = Counter(r["category"] for r in rows)
    attr = Counter(r["attribution"] for r in rows)
    successes = [r for r in rows if r["category"] == "success"]
    thin = attr["thin"]
    agent = attr["agent"]
    environment = attr["environment"]
    site = attr["site"]

    raw_success = len(successes) / n
    reachable = n - thin                       # exclude genuinely-thin sites
    adj_success = len(successes) / reachable if reachable else 0
    # "reliability on reachable, content-ful sites" — excludes thin + env + site
    controllable = n - thin - environment - site
    agent_success = (len(successes) / controllable) if controllable else 0

    avg_conf = sum(r["score"] for r in successes) / len(successes) if successes else 0
    avg_time = sum(r["seconds"] for r in rows) / n
    avg_quality = sum(r["quality"] for r in successes) / len(successes) if successes else 0

    # Industry performance
    by_ind = defaultdict(lambda: [0, 0])
    for r in rows:
        by_ind[r["industry"]][0] += 1
        if r["category"] == "success":
            by_ind[r["industry"]][1] += 1
    ind_rates = sorted(((ok / tot, ind, ok, tot)
                        for ind, (tot, ok) in by_ind.items()),
                       reverse=True)

    # Pages most used (sub-pages of successful crawls)
    sections = Counter()
    for r in successes:
        for u in r.get("pages_list", [])[1:]:   # skip homepage
            sections[_page_section(u)] += 1

    L = []
    L.append("# Research Agent — Reliability Evaluation Report\n")
    L.append(f"Companies tested: **{n}** (of {len(COMPANIES)} in the set)\n")
    L.append("## Headline")
    L.append(f"- **Raw success rate:** {raw_success*100:.1f}%  ({len(successes)}/{n})")
    L.append(f"- **Excluding genuinely-thin sites:** {adj_success*100:.1f}%  "
             f"({len(successes)}/{reachable})")
    L.append(f"- **On reachable, content-ful sites (excludes thin + site "
             f"bot-blocks + sandbox/env):** {agent_success*100:.1f}%  "
             f"({len(successes)}/{controllable})")
    L.append(f"- **True agent-fault failures:** {agent} "
             f"({agent/n*100:.1f}% of all) — json/extraction/unknown")
    L.append("")
    L.append("## Aggregates")
    L.append(f"- Average confidence (successes): **{avg_conf:.0f}/100**")
    L.append(f"- Average research quality (successes): **{avg_quality:.0f}/100**")
    L.append(f"- Average crawl time: **{avg_time:.1f}s**")
    email_ok = sum(1 for r in successes if r.get("email_status") == "ok")
    L.append(f"- Email generation success (of successes): "
             f"**{email_ok}/{len(successes)}**")
    L.append("")
    L.append("## Failure categories (root cause)")
    for category, count in cat.most_common():
        if category == "success":
            continue
        L.append(f"- `{category}` — {count} "
                 f"[{ATTRIBUTION.get(category, '?')}]")
    L.append("")
    L.append("## Attribution")
    for a in ("success", "thin", "site", "environment", "agent"):
        L.append(f"- {a}: {attr[a]} ({attr[a]/n*100:.1f}%)")
    L.append("")
    L.append("## Industries — best to worst")
    for rate, ind, ok, tot in ind_rates:
        L.append(f"- {ind}: {rate*100:.0f}%  ({ok}/{tot})")
    L.append("")
    L.append("## Pages most commonly used (beyond homepage)")
    for sec, count in sections.most_common(12):
        L.append(f"- /{sec}: {count}")
    L.append("")
    L.append("## Recommendations")
    for rec in _recommendations(cat, agent, n):
        L.append(f"- {rec}")
    L.append("")
    L.append("## Verdict")
    L.append(_verdict(agent_success, adj_success, agent, n))
    L.append("")
    L.append("## Per-company detail")
    L.append("| Company | Industry | Status | Category | Score | Qual | Pages | Time | Email |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        L.append(f"| {r['name']} | {r['industry']} | {r['status']} | "
                 f"{r['category']} | {r['score']} | {r['quality']} | "
                 f"{r['pages']} | {r['seconds']}s | {r.get('email_status')} |")

    report = "\n".join(L)
    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write(report)
    return report


def _recommendations(cat: Counter, agent: int, n: int) -> list:
    recs = []
    if cat.get("json_failure"):
        recs.append("`json_failure`: raise EVIDENCE_MAX_TOKENS and/or add a "
                    "partial-JSON salvage so a truncated response degrades to a "
                    "skip, never a hard error. ONE fix covers the whole category.")
    if cat.get("extraction_crash") or cat.get("unknown"):
        recs.append("`extraction_crash`/`unknown`: capture the raw exception in "
                    "the result so these can be diagnosed (currently masked as a "
                    "generic message).")
    if cat.get("empty_or_thin"):
        recs.append("`empty_or_thin`: verify Playwright render actually succeeded "
                    "on these (JS-render failure vs genuinely empty). One fix: log "
                    "render success/None per page (already partly logged).")
    if cat.get("bot_protection"):
        recs.append("`bot_protection` (403/429): enterprise sites (banks, law "
                    "firms, big manufacturers) use Akamai/Cloudflare. This is "
                    "SITE-side, not an agent bug — treat as out-of-ICP rather than "
                    "special-casing. Optionally add a realistic Accept-Language / "
                    "retry-after-render fallback (one systemic change).")
    if cat.get("timeout") or cat.get("connection_failure") or cat.get("dns_failure"):
        recs.append("`timeout`/`connection`/`dns`: largely THIS sandbox's degraded "
                    "network (seen returning 503s and DNS timeouts). Re-run from a "
                    "normal network before judging these against the agent.")
    if agent == 0:
        recs.append("No agent-fault failures observed — remaining failures are "
                    "thin/site/environment. Focus energy on ICP filtering, not "
                    "more crawler features.")
    return recs or ["No systemic issues detected."]


def _verdict(agent_success: float, adj_success: float, agent: int, n: int) -> str:
    if agent_success >= 0.95:
        return (f"**PRODUCTION-READY for MVP.** On reachable, content-ful sites "
                f"the agent succeeds {agent_success*100:.1f}% of the time, with "
                f"{agent} true agent-fault failure(s) across {n} companies. "
                f"Remaining misses are thin sites (no public info) or site/"
                f"environment issues outside the agent's control.")
    return (f"**NOT yet at the 95% bar.** Agent-controllable success is "
            f"{agent_success*100:.1f}%. Largest agent-fault categories should be "
            f"fixed first (see Recommendations) before adding any features.")


# ──────────────────────────────────────────────────────────────────────
def main():
    args = sys.argv[1:]
    if "--report" in args:
        print(generate_report())
        return
    limit = None
    if "--limit" in args:
        limit = int(args[args.index("--limit") + 1])
    do_email = "--no-email" not in args
    run(limit=limit, do_email=do_email)
    print("\n" + "=" * 60)
    print(generate_report().split("## Per-company detail")[0])
    print(f"(full report written to {REPORT})")


if __name__ == "__main__":
    main()
