"""Runnable entry point: research one company URL, then write the cold email.

Full flow:  research_company(url) -> research data -> write_email(data) -> print.

Usage (from the project root):
    python main.py                              # default example URL
    python main.py https://www.example.com      # research + write the email
    python main.py --reveal https://example.com # append the AI-reveal P.S.
    python main.py --find-founder https://x.com # also run the founder name-hunt
    python main.py --samples                    # write emails for 20 sample
                                                # research outputs (human review)
"""

import logging
import sys

from agents.research import research_company
from agents.writer import write_email

_LABELS = {
    "company_name": "Company",
    "founder_name": "Founder",
    "founder_role": "Founder role",
    "what_they_do": "What they do",
    "product_category": "Category",
    "business_model": "Business model",
    "company_stage": "Stage",
    "target_customer": "Target customer",
    "competitive_positioning": "Positioning",
    "recent_focus": "Recent focus",
    "unique_hook": "Unique hook",
    "their_mission_or_why": "Mission / why",
    "tone_style": "Tone / style",
    "metrics_or_traction": "Metrics",
    "pricing_model": "Pricing model",
}


def _print_list(label: str, items) -> None:
    items = items or []
    if items:
        print(f"  {label:<16}:")
        for item in items:
            print(f"      • {item}")
    else:
        print(f"  {label:<16}: —")


def _print_fields(data: dict) -> None:
    for key, label in _LABELS.items():
        value = data.get(key)
        print(f"  {label:<16}: {value if value is not None else '—'}")

    team = data.get("team_members") or []
    if team:
        print(f"  {'Team members':<16}:")
        for member in team:
            role = f" — {member['role']}" if member.get("role") else ""
            print(f"      • {member['name']}{role}")
    else:
        print(f"  {'Team members':<16}: —")

    _print_list("More hooks", data.get("additional_hooks"))
    _print_list("Notable custs", data.get("notable_customers"))
    _print_list("Industries", data.get("industries_served"))
    _print_list("Differentiators", data.get("product_differentiators"))
    _print_list("Pain points", data.get("pain_points"))
    _print_list("Integrations", data.get("integrations"))
    _print_list("Tech stack", data.get("tech_stack"))

    print(f"  {'Enough detail':<16}: {data.get('has_enough_detail')}")


def _print_result(url: str, result: dict) -> None:
    print("=" * 64)
    print(f"Researching: {url}")
    print("=" * 64)

    status = result.get("status")
    if status == "error":
        print(f"\n[ERROR] {result.get('error')}")
        print()
        return

    crawled = result.get("pages_crawled") or []
    if crawled:
        print(f"\nPages read ({len(crawled)}, via {result.get('fetch_method', '?')}):")
        for page in crawled:
            print(f"  - {page}")
    if result.get("stop_reason"):
        print(f"Stopped crawling because: {result['stop_reason']}")
    print(f"\nResearch score: {result.get('research_score', 0)}/100")

    if status == "skip":
        print(f"\n[SKIP]  {result.get('reason')}")
        if result.get("data"):
            print()
            _print_fields(result["data"])
    else:  # ok
        print("\n[OK]    Enough detail to personalize outreach.\n")
        _print_fields(result["data"])
        _print_top_hook(result.get("hooks") or [])
    print()


def _print_top_hook(hooks: list) -> None:
    """Show the auto-selected hook WITH its evidence (source + quote)."""
    if not hooks:
        return
    top = hooks[0]
    print("\n  Top hook (auto-selected):")
    print(f"      {top.get('text')}")
    print(f"      [{top.get('category')}] confidence {top.get('confidence')} "
          f"— {top.get('source')}")
    if top.get("quote"):
        print(f"      quote: \"{top['quote']}\"")


def _print_email(email: dict) -> None:
    """Pretty-print the writer's output (or its skip/error reason)."""
    print("=" * 64)
    print("COLD EMAIL")
    print("=" * 64)

    status = email.get("status")
    if status == "ok":
        who = email.get("to")
        company = email.get("company") or "?"
        print(f"\nTo: {who + ' @ ' if who else ''}{company}")
        print(f"Subject: {email.get('subject')}\n")
        print(email.get("body"))
        if email.get("used_reveal"):
            print("\n  (reveal mode ON — AI-reveal P.S. appended)")
    elif status == "skip":
        print(f"\n[SKIP]  {email.get('reason')}")
    else:
        print(f"\n[ERROR] {email.get('reason')}")
        for problem in email.get("problems") or []:
            print(f"        - {problem}")
    print()


def _run_one(url: str, add_reveal: bool, find_founder: bool = False) -> None:
    # research_company is designed never to raise for normal failures; this
    # guard is a final safety net so the CLI never dumps a traceback.
    try:
        result = research_company(url, find_founder=find_founder)
    except Exception as exc:  # noqa: BLE001 - last-resort guard
        print(f"[ERROR] Something went wrong: {exc}")
        sys.exit(1)
    _print_result(url, result)
    _print_email(write_email(result, add_reveal=add_reveal))


def _run_samples(add_reveal: bool) -> None:
    """Write an email for each of the 20 sample research outputs and print them
    for manual human review (requires a working ANTHROPIC_API_KEY)."""
    from tests.sample_research import SAMPLES  # local import: only for this mode

    print(f"Writing {len(SAMPLES)} sample emails for human review...\n")
    for i, sample in enumerate(SAMPLES, start=1):
        data = sample.get("data") or {}
        print("#" * 64)
        print(f"# SAMPLE {i:>2}/{len(SAMPLES)}: {data.get('company_name', '?')}")
        print("#" * 64)
        _print_email(write_email(sample, add_reveal=add_reveal))


def main() -> None:
    # Print UTF-8 cleanly on Windows consoles (default cp1252 mangles • and —).
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    args = sys.argv[1:]
    # --log turns on the research engine's INFO logs (to stderr) for debugging:
    # visited/skipped URLs, timeout reasons, extraction outcome, final score.
    if "--log" in args:
        logging.basicConfig(
            level=logging.INFO, stream=sys.stderr,
            format="%(levelname)s %(name)s: %(message)s",
        )
        args = [a for a in args if a != "--log"]
    add_reveal = "--reveal" in args
    args = [a for a in args if a != "--reveal"]
    # --find-founder opts into the dedicated founder/team name-hunt (extra Claude
    # calls). Off by default: a founder plainly on a page is still surfaced.
    find_founder = "--find-founder" in args
    args = [a for a in args if a != "--find-founder"]

    if args and args[0] == "--samples":
        _run_samples(add_reveal)
        return

    url = args[0] if args else "https://www.anthropic.com"
    _run_one(url, add_reveal, find_founder)


if __name__ == "__main__":
    main()
