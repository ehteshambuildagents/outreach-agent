r"""Accuracy harness for the Research agent.

Runs research_company() over a batch of SaaS URLs, prints a summary table and
success/skip/error rates, and saves every full extraction to results.txt so you
can manually verify each one against the real website (hallucination check).

USAGE (from the project root):
    # 1) Use the built-in default list:
    python test_accuracy.py

    # 2) Pass your own URLs directly:
    python test_accuracy.py https://site-a.com https://site-b.com ...

    # 3) Put one URL per line in a file (blank lines and #comments ignored):
    python test_accuracy.py urls.txt

With the project venv that's:
    .\.venv\Scripts\python.exe test_accuracy.py urls.txt
"""

import datetime as _dt
import os
import sys
import time

from agents.research import research_company

# Replace these with your own 15-20 URLs, or pass a file / args instead.
DEFAULT_URLS = [
    "https://plausible.io",
    "https://www.tability.io",
    "https://ghost.org",
    "https://posthog.com",
    "https://www.cal.com",
    "https://www.gumroad.com",
    "https://transistor.fm",
    "https://www.hey.com",
    "https://tailscale.com",
    "https://linear.app",
    "https://www.fly.io",
    "https://render.com",
    "https://supabase.com",
    "https://www.bear.app",
    "https://buttondown.com",
    "https://www.notion.so",
    "https://savvycal.com",
    "https://www.fathomanalytics.com",
]

RESULTS_FILE = "results.txt"


# ──────────────────────────────────────────────────────────────────────
#  Input handling
# ──────────────────────────────────────────────────────────────────────
def load_urls(args):
    """Resolve the URL list from a file, CLI args, or the default list."""
    if len(args) == 1 and os.path.isfile(args[0]):
        with open(args[0], encoding="utf-8") as handle:
            urls = [
                line.strip()
                for line in handle
                if line.strip() and not line.strip().startswith("#")
            ]
        return urls, f"file '{args[0]}'"
    if args:
        return args, "command-line arguments"
    return list(DEFAULT_URLS), "built-in default list"


# ──────────────────────────────────────────────────────────────────────
#  Per-result helpers
# ──────────────────────────────────────────────────────────────────────
def hook_count(data):
    """Total specific hooks = unique_hook (if any) + additional_hooks."""
    if not data:
        return 0
    count = 1 if data.get("unique_hook") else 0
    count += len(data.get("additional_hooks") or [])
    return count


def team_count(data):
    """Number of named team members extracted."""
    if not data:
        return 0
    return len(data.get("team_members") or [])


def has_person(data):
    """True if a founder name or any team member was found."""
    if not data:
        return False
    return bool(data.get("founder_name")) or bool(data.get("team_members"))


def _truncate(value, width):
    text = "" if value is None else str(value)
    text = " ".join(text.split())  # flatten any newlines
    return text if len(text) <= width else text[: width - 1] + "…"


# ──────────────────────────────────────────────────────────────────────
#  Output: summary table
# ──────────────────────────────────────────────────────────────────────
def print_table(rows):
    header = (
        f"{'#':<3} {'URL':<26} {'STATUS':<6} {'PATH':<8} {'SCORE':>5} "
        f"{'COMPANY':<18} {'FOUNDER':<16} {'TEAM':>4} {'HOOKS':>5}"
    )
    print("\n" + header)
    print("-" * len(header))
    for i, row in enumerate(rows, start=1):
        result = row["result"]
        data = result.get("data") or {}
        print(
            f"{i:<3} {_truncate(row['url'], 26):<26} {_status_label(result):<6} "
            f"{result.get('fetch_method', '?'):<8} "
            f"{result.get('research_score', 0):>5} "
            f"{_truncate(data.get('company_name'), 18):<18} "
            f"{_truncate(data.get('founder_name') or '—', 16):<16} "
            f"{team_count(data):>4} {hook_count(data):>5}"
        )


def _status_label(result):
    return {"ok": "OK", "skip": "SKIP", "error": "ERROR"}.get(
        result.get("status"), "?"
    )


# ──────────────────────────────────────────────────────────────────────
#  Output: aggregate stats
# ──────────────────────────────────────────────────────────────────────
def print_stats(rows):
    total = len(rows)
    if total == 0:
        print("\nNo URLs to test.")
        return

    statuses = [r["result"].get("status") for r in rows]
    ok = statuses.count("ok")
    skip = statuses.count("skip")
    error = statuses.count("error")

    founders = sum(
        1 for r in rows if (r["result"].get("data") or {}).get("founder_name")
    )
    persons = sum(1 for r in rows if has_person(r["result"].get("data")))

    def pct(n):
        return f"{n / total * 100:5.1f}%  ({n}/{total})"

    print("\n" + "=" * 48)
    print("  ACCURACY SUMMARY")
    print("=" * 48)
    print(f"  SUCCESS RATE (OK)        : {pct(ok)}")
    print(f"  SKIP RATE                : {pct(skip)}")
    print(f"  ERROR RATE               : {pct(error)}")
    print("-" * 48)
    print(f"  Founder name found       : {pct(founders)}")
    print(f"  Founder OR team found    : {pct(persons)}")
    print(f"  No person found          : {pct(total - persons)}")
    print("-" * 48)
    fast = sum(1 for r in rows if r["result"].get("fetch_method") == "fast")
    rendered = sum(1 for r in rows if r["result"].get("fetch_method") == "rendered")
    print(f"  Fast path (no browser)   : {pct(fast)}")
    print(f"  Rendered (browser used)  : {pct(rendered)}")
    print("=" * 48)
    print(
        f"\nFull details saved to '{RESULTS_FILE}'. Open it and compare each\n"
        "entry against the real website to check for hallucinations."
    )


# ──────────────────────────────────────────────────────────────────────
#  Output: results.txt (full detail for manual verification)
# ──────────────────────────────────────────────────────────────────────
def _append_list(lines, label, items):
    items = items or []
    if items:
        lines.append(f"{label:<18} :")
        lines.extend(f"  • {item}" for item in items)
    else:
        lines.append(f"{label:<18} : (none)")


def write_results_file(rows, source_desc):
    lines = []
    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines.append("Research agent — full extraction results")
    lines.append(f"Generated: {stamp}")
    lines.append(f"URL source: {source_desc}")
    lines.append(f"Sites tested: {len(rows)}")
    lines.append(
        "\nTo check for hallucinations: open each company's real website and\n"
        "confirm every field below is actually stated there. Anything not on\n"
        "the site is a hallucination and should be reported.\n"
    )

    for i, row in enumerate(rows, start=1):
        result = row["result"]
        lines.append("=" * 70)
        lines.append(f"[{i}] {row['url']}")
        lines.append(
            f"Status: {_status_label(result)}    "
            f"Path: {result.get('fetch_method', '?')}    ({row['seconds']:.1f}s)"
        )

        if result.get("status") == "error":
            lines.append(f"Error: {result.get('error')}")
            lines.append("")
            continue

        crawled = result.get("pages_crawled") or []
        if crawled:
            lines.append("Pages crawled:")
            lines.extend(f"  - {page}" for page in crawled)

        if result.get("status") == "skip":
            lines.append(f"Skip reason: {result.get('reason')}")

        data = result.get("data")
        if not data:
            lines.append("(no extracted data)")
            lines.append("")
            continue

        lines.append(f"Company name       : {data.get('company_name')}")
        lines.append(f"Founder name       : {data.get('founder_name')}")
        lines.append(f"Founder role       : {data.get('founder_role')}")
        team = data.get("team_members") or []
        if team:
            lines.append("Team members       :")
            for member in team:
                role = f" — {member['role']}" if member.get("role") else ""
                lines.append(f"  • {member['name']}{role}")
        else:
            lines.append("Team members       : (none found)")
        lines.append(f"What they do       : {data.get('what_they_do')}")
        lines.append(f"Target customer    : {data.get('target_customer')}")
        lines.append(f"Recent focus       : {data.get('recent_focus')}")
        lines.append(f"Unique hook        : {data.get('unique_hook')}")
        _append_list(lines, "Additional hooks", data.get("additional_hooks"))
        lines.append(f"Mission / why      : {data.get('their_mission_or_why')}")
        lines.append(f"Tone / style       : {data.get('tone_style')}")
        lines.append(f"Pricing model      : {data.get('pricing_model')}")
        lines.append(f"Metrics/traction   : {data.get('metrics_or_traction')}")
        _append_list(lines, "Notable customers", data.get("notable_customers"))
        _append_list(lines, "Tech/product", data.get("tech_stack"))
        lines.append(f"Research score     : {result.get('research_score', 0)}/100")
        lines.append(f"Has enough detail  : {data.get('has_enough_detail')}")

        hooks = result.get("hooks") or []
        if hooks:
            lines.append("Ranked hooks (with evidence):")
            for h in hooks[:6]:
                lines.append(f"  • [{h.get('category')}] {h.get('text')}")
                lines.append(f"      source: {h.get('source')}")
                if h.get("quote"):
                    lines.append(f"      quote : \"{h.get('quote')}\"")

        evidence = result.get("evidence") or {}
        if evidence:
            lines.append("Evidence (verify each value against the source quote):")
            for fieldname, items in evidence.items():
                best = items[0] if items else None
                if best:
                    lines.append(f"  - {fieldname}: {best.get('value')}")
                    lines.append(f"      source: {best.get('source')}  "
                                 f"(conf {best.get('confidence')}, "
                                 f"corrob {best.get('corroborations', 1)}"
                                 f"{', CONFLICT' if best.get('conflict') else ''})")
                    if best.get("quote"):
                        lines.append(f"      quote : \"{best.get('quote')}\"")
        lines.append("")

    with open(RESULTS_FILE, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


# ──────────────────────────────────────────────────────────────────────
#  Driver
# ──────────────────────────────────────────────────────────────────────
def run(urls, source_desc):
    rows = []
    total = len(urls)
    print(f"Testing {total} site(s) from {source_desc}...\n")

    for i, url in enumerate(urls, start=1):
        print(f"[{i:>2}/{total}] {url} ...", end="", flush=True)
        start = time.perf_counter()
        try:
            result = research_company(url)
        except Exception as exc:  # research_company shouldn't raise, but be safe
            result = {"status": "error", "error": f"Harness caught: {exc}"}
        seconds = time.perf_counter() - start
        rows.append({"url": url, "result": result, "seconds": seconds})
        print(f" {_status_label(result)}  ({seconds:.1f}s)")

    print_table(rows)
    print_stats(rows)
    write_results_file(rows, source_desc)
    return rows


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # clean • and — on Windows
    except Exception:
        pass

    urls, source_desc = load_urls(sys.argv[1:])
    if not urls:
        print("No URLs provided. Pass URLs, a urls.txt file, or edit DEFAULT_URLS.")
        sys.exit(1)
    run(urls, source_desc)


if __name__ == "__main__":
    main()
