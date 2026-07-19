"""Launch-day broadcast — email every confirmed subscriber exactly once.

Run it:

    python -m waitlist.broadcast                 # dry run: report only, sends nothing
    python -m waitlist.broadcast --send          # actually send
    python -m waitlist.broadcast --send --limit 50

Dry run is the default deliberately. This is the one irreversible action in the
whole waitlist feature: you cannot unsend a launch announcement, and getting it
wrong burns both the list and the sending domain. You have to ask for it.

Resumability is the other half. ``notified_at`` is stamped per address as each
send succeeds, and the query selects only ``status='subscribed' AND notified_at IS
NULL``. So an interrupted run, a crash, or simply running it twice will never
double-send: the second run picks up exactly the addresses the first did not
finish. A send that fails leaves ``notified_at`` unset, so it is retried next run
rather than silently skipped.
"""

import argparse
import logging
import sys
import time

from waitlist import email as mailer
from waitlist import launch_message, store

log = logging.getLogger("saqua.waitlist.broadcast")

# Gentle default. Resend's own limit is far higher, but a launch blast that
# arrives as one burst is exactly the shape that trips spam filtering.
DEFAULT_SLEEP = 0.6


def run(*, send: bool = False, limit: int = 1000, sleep: float = DEFAULT_SLEEP,
        db=None) -> dict:
    rows = store.pending_broadcast(db=db, limit=limit)
    summary = {"pending": len(rows), "sent": 0, "failed": 0, "dry_run": not send}

    counts = store.counts(db=db)
    log.info("waitlist status: %s", counts or "{}")
    log.info("%d confirmed subscriber(s) awaiting the launch email", len(rows))

    if not send:
        for r in rows[:10]:
            log.info("  would email: %s", r["email"])
        if len(rows) > 10:
            log.info("  ... and %d more", len(rows) - 10)
        log.info("DRY RUN — nothing sent. Re-run with --send to actually send.")
        return summary

    if not mailer.configured():
        log.error("Resend is not configured (RESEND_API_KEY / WAITLIST_FROM_EMAIL). "
                  "Refusing to run.")
        summary["failed"] = len(rows)
        return summary

    for row in rows:
        subject, html, text, headers = launch_message(row)
        ok, detail = mailer.send(row["email"], subject, html, text=text,
                                 headers=headers)
        if ok:
            # Stamp only after the provider accepted it. If this stamp fails the
            # address is retried next run — a duplicate is recoverable, a silent
            # drop is not.
            store.mark_notified(row["email"], db=db)
            summary["sent"] += 1
            log.info("sent: %s", row["email"])
        else:
            summary["failed"] += 1
            log.warning("failed: %s (%s)", row["email"], detail)
        time.sleep(sleep)

    log.info("broadcast complete: %d sent, %d failed", summary["sent"],
             summary["failed"])
    return summary


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    p = argparse.ArgumentParser(description="Send the Saqua launch email to the waitlist.")
    p.add_argument("--send", action="store_true",
                   help="actually send (default is a dry run)")
    p.add_argument("--limit", type=int, default=1000,
                   help="max addresses this run (default 1000)")
    p.add_argument("--sleep", type=float, default=DEFAULT_SLEEP,
                   help=f"seconds between sends (default {DEFAULT_SLEEP})")
    args = p.parse_args(argv)

    result = run(send=args.send, limit=args.limit, sleep=args.sleep)
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
