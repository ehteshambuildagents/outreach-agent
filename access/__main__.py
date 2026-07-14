"""Internal CLI for request-access gating (soft launch).

    python -m access pending            # list users awaiting approval
    python -m access list               # list every known user + status
    python -m access approve <user_id>  # grant full access
    python -m access deny    <user_id>  # reject

Talks straight to the database (no HTTP), so it works in any environment — this is
the "basic internal list view where you can flip a user to approved."
"""

import sys

import access


def _fmt(rows) -> None:
    if not rows:
        print("(none)")
        return
    for r in rows:
        email = r.get("email") or "-"
        print(f"  {r['status']:<9} {r['user_id']:<40} {email}")


def main(argv) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == "pending":
        print("Pending users:")
        _fmt(access.list_pending())
        return 0
    if cmd == "list":
        print("All users:")
        _fmt(access.list_all())
        return 0
    if cmd == "approve" and len(argv) > 1:
        access.approve(argv[1], " ".join(argv[2:]) or None)
        print(f"approved {argv[1]}")
        return 0
    if cmd == "deny" and len(argv) > 1:
        access.deny(argv[1], " ".join(argv[2:]) or None)
        print(f"denied {argv[1]}")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
