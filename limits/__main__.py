"""Internal CLI for per-user usage caps + the account kill switch.

    python -m limits usage             # usage-vs-cap for everyone active in 24h
    python -m limits usage <user_id>   # one user's usage-vs-cap
    python -m limits pause  <user_id> [reason]
    python -m limits resume <user_id>

Talks straight to the database (no HTTP), so it works in any environment.
"""

import json
import sys

import limits


def _print_snapshot(s: dict) -> None:
    flag = "  ⛔ PAUSED" if s.get("state") == "paused" else ""
    print(f"\n{s['user_id']}{flag}")
    print(f"  spend today:  ${s['daily_spend']:.4f} / ${s['daily_cap']:.2f}"
          f"   month: ${s['monthly_spend']:.4f} / ${s['monthly_cap']:.2f}"
          f"   last-hour calls: {s['hour_calls']}")
    for prov, p in sorted(s["providers"].items()):
        bar = "OVER" if p["daily_cap"] and p["calls_today"] >= p["daily_cap"] else ""
        print(f"    {prov:<11} {p['calls_today']:>5} / {p['daily_cap']:<5} calls"
              f"   (${p['cost_today']:.4f}) {bar}")


def main(argv) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == "usage":
        if len(argv) > 1:
            _print_snapshot(limits.usage_snapshot(argv[1]))
        else:
            rows = limits.all_usage()
            if not rows:
                print("No usage recorded in the last 24h.")
            for s in rows:
                _print_snapshot(s)
        return 0
    if cmd == "pause" and len(argv) > 1:
        limits.pause(argv[1], " ".join(argv[2:]) or "paused by admin")
        print(json.dumps(limits.usage_snapshot(argv[1]), indent=2))
        return 0
    if cmd == "resume" and len(argv) > 1:
        limits.resume(argv[1])
        print(f"resumed {argv[1]} -> state={limits.usage_snapshot(argv[1])['state']}")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
