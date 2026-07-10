"""Run the recipient-discovery MVP validation campaign.

This is an eval harness, not product code. It reads the previous campaign's
company list for an apples-to-apples live rerun, then enforces the launch path:

Research -> Qualification gate -> Strategy -> Writer -> Guard.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

os.environ.setdefault("TELEMETRY_SYNC", "1")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents import qualification, strategy  # noqa: E402
from agents.research import research_company  # noqa: E402
from agents.writer import write_email  # noqa: E402
from guard import assess as guard_assess  # noqa: E402
from telemetry import flush, query, scope  # noqa: E402


OLD_JSONL = ROOT / "eval" / "mvp_validation_20260708.jsonl"
BASELINE_JSONL = ROOT / "eval" / "mvp_validation_20260708_gated.jsonl"
OUT_JSONL = ROOT / "eval" / "mvp_validation_20260708_recipient.jsonl"
OUT_MD = ROOT / "eval" / "mvp_validation_20260708_recipient.md"
CAMPAIGN_ID = "mvp_validation_20260708_recipient"

PASS_RECS = {qualification.CONTINUE, qualification.HIGH_PRIORITY}
SEND_ACTIONS = {strategy.DRAFT, strategy.SEQUENCE}


def _load_targets(limit: int = 32) -> list[dict]:
    rows = []
    seen = set()
    for line in OLD_JSONL.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        domain = row.get("domain")
        url = row.get("url") or (f"https://{domain}" if domain else None)
        if not url or url in seen:
            continue
        seen.add(url)
        rows.append({
            "input_name": row.get("input_name") or row.get("company_name") or domain,
            "url": url,
            "domain": domain,
            "industry": row.get("industry") or "SaaS",
        })
        if len(rows) >= limit:
            break
    return rows


def _data(research: dict) -> dict:
    return (research or {}).get("data") or {}


def _summary(data: dict) -> str | None:
    parts = [data.get("what_they_do"), data.get("target_customer"), data.get("unique_hook")]
    return " | ".join(str(p).strip() for p in parts if str(p or "").strip()) or None


def _guard_input(email: dict, research: dict, qual: dict, strat: dict) -> dict:
    data = _data(research)
    return {
        "email": {
            "subject": email.get("subject") or "",
            "body": email.get("body") or "",
            "to": email.get("to") or "",
            "company": email.get("company") or data.get("company_name") or "",
        },
        "writer": {"status": email.get("status"), "reason": email.get("reason")},
        "qualification": qual or {},
        "strategy": strat or {},
        "research": {"company_name": data.get("company_name")},
        "personalization": {
            "specific": bool(data.get("unique_hook") or data.get("additional_hooks")),
            "based_on_research": True,
            "generic": False,
        },
    }


def _rate_email(row: dict) -> tuple[str, str, bool]:
    body = (row.get("generated_email_body") or "").strip()
    subject = (row.get("generated_subject") or "").strip()
    if row.get("final_classification") != "sendable":
        if body:
            return "Average", "draft exists but pipeline/guard says do not send", False
        return "Unusable", row.get("final_reason") or "no sendable email", False
    low = body.lower()
    words = body.split()
    if not subject or not body:
        return "Unusable", "empty subject or body", False
    if len(words) < 25:
        return "Poor", "too short to be useful", False
    generic_hits = sum(1 for p in (
        "hope you're well", "quick question", "i wanted to reach out",
        "i help founders get replies", "book a call", "are you free"
    ) if p in low)
    has_specific = bool(row.get("strategy_angle")) and (
        str(row.get("strategy_angle")).split(" ")[0].lower().strip(".,") in low
        or bool(row.get("research_unique_hook"))
    )
    if generic_hits >= 2:
        return "Poor", "spammy/generic cold email patterns", False
    if has_specific and len(words) <= 110 and "?" in body:
        return "Good", "specific, concise, with a low-friction CTA", True
    if has_specific:
        return "Good", "specific and usable, though CTA/flow may need review", True
    return "Average", "readable but not strongly tied to research", False


def _run_one(index: int, target: dict) -> dict:
    started = time.perf_counter()
    before_cost = query.campaign_cost(CAMPAIGN_ID)
    before_tokens = query.total_tokens()
    row = {
        "index": index,
        "input_name": target["input_name"],
        "domain": target.get("domain"),
        "url": target["url"],
        "industry": target.get("industry"),
        "final_classification": None,
        "final_reason": None,
    }

    with scope(campaign_id=CAMPAIGN_ID, user_id="eval", agent="mvp_validation"):
        research = research_company(target["url"])
    row["research_status"] = research.get("status")
    row["status"] = research.get("status")
    row["pages_crawled"] = len(research.get("pages_crawled") or [])
    row["pages_list"] = research.get("pages_crawled") or []
    row["fetch_method"] = research.get("fetch_method")
    row["research_score"] = research.get("research_score")
    data = _data(research)
    row["company_name"] = data.get("company_name") or target["input_name"]
    row["research_summary"] = _summary(data)
    row["research_unique_hook"] = data.get("unique_hook")
    row["named_person"] = data.get("primary_contact_name") or data.get("founder_name")
    row["founder"] = data.get("founder_name")
    row["decision_maker_found"] = bool(data.get("decision_maker_found"))
    row["recipient_route"] = data.get("recipient_route")
    row["public_contact_email"] = data.get("public_contact_email")
    row["contact_page_url"] = data.get("contact_page_url")
    row["linkedin_url"] = data.get("linkedin_url")

    if research.get("status") != "ok":
        row["final_classification"] = "research_failed"
        row["final_reason"] = research.get("reason") or research.get("error") or "research did not return ok"
        return _finish(row, started, before_cost, before_tokens)

    qual = qualification.qualify(research=research).to_dict()
    row["qualification"] = qual
    row["qualification_recommendation"] = qual.get("recommendation")
    row["qualification_score"] = qual.get("qualification_score")
    row["qualification_allowed_progression"] = qual.get("recommendation") in PASS_RECS and qual.get("confidence", 0) >= 40
    if not row["qualification_allowed_progression"]:
        row["final_classification"] = "qualification_blocked"
        row["final_reason"] = f"qualification={qual.get('recommendation')}"
        return _finish(row, started, before_cost, before_tokens)

    strat_obj = strategy.decide(research=research, qualification=qual)
    strat = strat_obj.to_dict()
    row["strategy"] = strat
    row["strategy_decision"] = strat.get("recommended_action")
    row["strategy_action"] = strat.get("recommended_action")
    row["strategy_angle"] = strat.get("primary_hook")
    if strat.get("recommended_action") not in SEND_ACTIONS:
        row["final_classification"] = "strategy_hold"
        row["final_reason"] = f"strategy={strat.get('recommended_action')}"
        return _finish(row, started, before_cost, before_tokens)

    source = dict(research)
    source["qualification"] = qual
    source["strategy"] = strat
    with scope(campaign_id=CAMPAIGN_ID, user_id="eval", agent="writer"):
        email = write_email(source)
    row["email"] = email
    row["writer_status"] = email.get("status")
    row["generated_subject"] = email.get("subject")
    row["generated_email_body"] = email.get("body")
    if email.get("status") != "ok":
        row["final_classification"] = "writer_failed"
        row["final_reason"] = email.get("reason") or "writer did not return ok"
        return _finish(row, started, before_cost, before_tokens)

    guard = guard_assess(_guard_input(email, research, qual, strat))
    row["guard"] = guard
    row["guard_decision"] = guard.get("decision")
    row["guard_risk"] = guard.get("overallRisk")
    row["guard_issues"] = (guard.get("deliverability", {}).get("issues") or []) + (
        guard.get("cost", {}).get("issues") or [])
    if guard.get("decision") == "BLOCK":
        row["final_classification"] = "guard_blocked"
        row["final_reason"] = "; ".join(row["guard_issues"][:3]) or "guard blocked"
    else:
        row["final_classification"] = "sendable"
        row["final_reason"] = "guard allowed"
    return _finish(row, started, before_cost, before_tokens)


def _finish(row: dict, started: float, before_cost: float, before_tokens: int) -> dict:
    flush()
    row["latency"] = round(time.perf_counter() - started, 2)
    row["estimated_cost"] = round(max(0.0, query.campaign_cost(CAMPAIGN_ID) - before_cost), 6)
    row["tokens"] = max(0, query.total_tokens() - before_tokens)
    rating, reason, good = _rate_email(row)
    row["manual_rating"] = rating
    row["manual_judgment_reason"] = reason
    row["good_enough_to_send"] = good
    return row


def _summarize(rows: list[dict]) -> dict:
    n = len(rows)
    counts = Counter(r.get("final_classification") for r in rows)
    ratings = Counter(r.get("manual_rating") for r in rows)
    qual_passed = [r for r in rows if r.get("qualification_allowed_progression")]
    good_passed = [r for r in qual_passed if r.get("manual_rating") in ("Excellent", "Good")]
    missing_recipient_blocks = sum(
        1 for r in rows
        if r.get("final_classification") == "guard_blocked"
        and any("recipient" in str(i).lower() for i in (r.get("guard_issues") or []))
    )
    return {
        "companies": n,
        "classifications": dict(counts),
        "ratings": dict(ratings),
        "research_failed": counts.get("research_failed", 0),
        "writer_failed": counts.get("writer_failed", 0),
        "guard_blocked": counts.get("guard_blocked", 0),
        "missing_recipient_guard_blocks": missing_recipient_blocks,
        "qualification_blocked": counts.get("qualification_blocked", 0),
        "sendable": counts.get("sendable", 0),
        "excellent_good": ratings.get("Excellent", 0) + ratings.get("Good", 0),
        "usable_pct_all": round(100 * sum(1 for r in rows if r.get("good_enough_to_send")) / n, 1) if n else 0,
        "excellent_good_pct_qualified": round(100 * len(good_passed) / len(qual_passed), 1) if qual_passed else 0,
        "avg_cost": round(sum(float(r.get("estimated_cost") or 0) for r in rows) / n, 4) if n else 0,
        "avg_latency": round(sum(float(r.get("latency") or 0) for r in rows) / n, 1) if n else 0,
    }


def _load_baseline_summary() -> dict:
    if not BASELINE_JSONL.exists():
        return {}
    rows = [json.loads(line) for line in BASELINE_JSONL.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    return _summarize(rows)


def _write_report(rows: list[dict], summary: dict) -> None:
    baseline = _load_baseline_summary()
    lines = [
        "# MVP Validation - Recipient Discovery Fix",
        "",
        f"Companies evaluated: {summary['companies']}",
        f"Final classifications: {summary['classifications']}",
        f"Rating breakdown: {summary['ratings']}",
        f"Excellent+Good: {summary['excellent_good']} ({summary['usable_pct_all']}% of all companies)",
        f"Excellent+Good among qualification-passed: {summary['excellent_good_pct_qualified']}%",
        f"Guard blocks: {summary['guard_blocked']}",
        f"Missing-recipient guard blocks: {summary['missing_recipient_guard_blocks']}",
        f"Average estimated cost / prospect: ${summary['avg_cost']}",
        f"Average latency / prospect: {summary['avg_latency']}s",
        "",
    ]
    if baseline:
        lines.extend([
            "## Gated Baseline Comparison",
            "",
            f"Baseline guard blocks: {baseline.get('guard_blocked')} -> {summary['guard_blocked']}",
            f"Baseline missing-recipient guard blocks: "
            f"{baseline.get('missing_recipient_guard_blocks')} -> "
            f"{summary['missing_recipient_guard_blocks']}",
            f"Baseline writer failures: {baseline.get('writer_failed')} -> {summary['writer_failed']}",
            f"Baseline Excellent+Good: {baseline.get('excellent_good')} -> {summary['excellent_good']}",
            f"Baseline avg cost: ${baseline.get('avg_cost')} -> ${summary['avg_cost']}",
            f"Baseline avg latency: {baseline.get('avg_latency')}s -> {summary['avg_latency']}s",
            "",
        ])
    lines.extend([
        "| # | Company | Domain | Research | Qual | Strategy | Guard | Final | Rating | Cost | Latency |",
        "|---:|---|---|---|---|---|---|---|---|---:|---:|",
    ])
    for r in rows:
        lines.append(
            f"| {r['index']} | {r.get('company_name') or ''} | {r.get('domain') or ''} | "
            f"{r.get('research_status') or ''} | {r.get('qualification_recommendation') or ''} "
            f"({r.get('qualification_score') if r.get('qualification_score') is not None else ''}) | "
            f"{r.get('strategy_action') or ''} | {r.get('guard_decision') or ''} "
            f"({r.get('guard_risk') if r.get('guard_risk') is not None else ''}) | "
            f"{r.get('final_classification') or ''} | {r.get('manual_rating') or ''} | "
            f"${r.get('estimated_cost') or 0} | {r.get('latency') or 0}s |"
        )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    targets = _load_targets()
    OUT_JSONL.write_text("", encoding="utf-8")
    rows = []
    for i, target in enumerate(targets, start=1):
        print(f"[{i}/{len(targets)}] {target['input_name']} {target['url']}", flush=True)
        row = _run_one(i, target)
        rows.append(row)
        with OUT_JSONL.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(
            f"  -> {row['final_classification']} rating={row['manual_rating']} "
            f"cost=${row['estimated_cost']} latency={row['latency']}s",
            flush=True,
        )
    summary = _summarize(rows)
    _write_report(rows, summary)
    print(json.dumps(summary, indent=2), flush=True)
    print(f"Wrote {OUT_JSONL}")
    print(f"Wrote {OUT_MD}")


if __name__ == "__main__":
    main()
