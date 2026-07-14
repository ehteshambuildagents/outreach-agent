"""Inbound push notifications -> reply detection.

Gmail Pub/Sub and Microsoft Graph notify us only that *something changed*; this
module resolves each notification into concrete inbound messages, matches them to
a running workflow by thread, and calls :func:`engine.ingest_reply` (which stops
the sequence idempotently). Kept separate from the HTTP layer so the resolution
logic is unit-testable with a fake provider and a real store.

Every path is duplicate-safe (durable ledger keyed on notification + message id)
and restart-safe (the store, not memory, holds the last historyId / state). Our
own outbound messages are filtered out so a send never looks like a reply.
"""

import base64
import json
import logging

from automation import engine, metrics
from automation.providers import get_provider

log = logging.getLogger("automation.push")

# Bumped on every deploy-relevant change to this module so a stale build is obvious
# from the boot log alone: server.api logs this value at startup. If the deploy is
# serving an old push.py (build-layer or .pyc cache), the marker in the boot log
# won't match this value (or the attribute is missing) — no test push needed.
BUILD_MARKER = "2026-07-14-trace-v2"


# ── Gmail (Cloud Pub/Sub push) ─────────────────────────────────────────
def decode_pubsub(envelope: dict) -> dict:
    """Extract {emailAddress, historyId} from a Pub/Sub push envelope. {} if bad."""
    try:
        data = ((envelope or {}).get("message") or {}).get("data")
        if not data:
            return {}
        decoded = base64.b64decode(data).decode("utf-8")
        payload = json.loads(decoded)
        return {"email": (payload.get("emailAddress") or "").lower(),
                "history_id": payload.get("historyId")}
    except (ValueError, TypeError, json.JSONDecodeError):
        return {}


def handle_gmail_pubsub(store, token_store, envelope: dict, *, provider_factory=None) -> dict:
    # Every step is logged on automation.push AND appended to a `trace` list that is
    # RETURNED to the caller, so the webhook route can re-log the trace on a logger
    # that IS visible in the deploy (automation.push is currently dropped in prod).
    # Logic (matching / stopping) is unchanged — this only adds observability.
    trace = []

    def _t(msg, *args):
        line = (msg % args) if args else msg
        log.info("gmail push: %s", line)
        trace.append(line)

    if isinstance(envelope, dict):
        _msg = envelope.get("message")
        _t("envelope keys=%s; message keys=%s", list(envelope.keys()),
           list(_msg.keys()) if isinstance(_msg, dict) else type(_msg).__name__)
    else:
        _t("envelope is NOT a dict: type=%s repr=%.200r", type(envelope).__name__, envelope)
    provider_factory = provider_factory or (lambda tok: get_provider("gmail", credentials=tok))
    info = decode_pubsub(envelope)
    if not info.get("email"):
        _t("unparseable envelope, ignoring (no email/historyId)")
        return {"ok": True, "ignored": "unparseable", "trace": trace}
    _t("received for %s (history_id=%s)", info["email"], info.get("history_id"))
    # Duplicate Pub/Sub delivery for the same (mailbox, historyId) is a no-op.
    dedup = f"gmailpush:{info['email']}:{info['history_id']}"
    if not store.mark_processed(dedup):
        _t("duplicate delivery for %s history_id=%s, skipping", info["email"], info["history_id"])
        return {"ok": True, "duplicate": True, "trace": trace}

    accounts = token_store.accounts_by_email("gmail", info["email"])
    _t("%d connected account(s) match %s", len(accounts), info["email"])
    stopped = 0
    for acct in accounts:
        token = token_store.valid_access_token(acct["user_id"], "gmail",
                                               acct["account_email"])
        if not token:
            _t("no valid token for %s (reconnect required), skipping", acct["account_email"])
            continue                       # reconnect needed; nothing to poll
        # watch() seeds the id under Gmail's camelCase "historyId"; each processed
        # push then tracks it under "history_id". Read either (prefer the tracked
        # value) and seed the FIRST push from the watch() response — NOT the push's
        # own current historyId, which returns no new changes and misses the reply.
        ws = acct.get("watch_state") or {}
        start = ws.get("history_id") or ws.get("historyId") or info["history_id"]
        try:
            hist = provider_factory(token).list_history(start)
        except Exception as exc:           # noqa: BLE001
            metrics.incr("provider_failures")
            _t("history.list FAILED for %s startHistoryId=%s: %s: %s",
               acct["account_email"], start, type(exc).__name__, exc)
            continue
        messages = hist.get("messages", [])
        _t("history.list for %s startHistoryId=%s returned %d message(s) (new history_id=%s)",
           acct["account_email"], start, len(messages), hist.get("history_id"))
        for msg in messages:
            mid, tid = msg.get("message_id"), msg.get("thread_id")
            if "SENT" in (msg.get("labels") or []):
                _t("  msg %s thread %s: SENT (our own), skipped", mid, tid)
                continue                   # our own outbound, not a reply
            wf = engine.ingest_reply(store, message_id=mid,
                                     user_id=acct["user_id"], thread_id=tid)
            if wf is None:
                _t("  msg %s thread %s: ingest_reply -> NO matching workflow", mid, tid)
            elif wf.state == "STOPPED":
                stopped += 1
                _t("  msg %s thread %s: ingest_reply -> STOPPED workflow %s", mid, tid, wf.id)
            else:
                _t("  msg %s thread %s: ingest_reply -> matched workflow %s state=%s (not stopped)",
                   mid, tid, wf.id, wf.state)
        # advance stored historyId so the next push starts from here
        if hist.get("history_id"):
            state = dict(acct.get("watch_state") or {})
            state["history_id"] = hist["history_id"]
            token_store.set_watch_state(acct["user_id"], "gmail",
                                        acct["account_email"], state)
            _t("advanced stored history_id to %s for %s", hist["history_id"], acct["account_email"])
    _t("done for %s, stopped %d sequence(s)", info["email"], stopped)
    return {"ok": True, "stopped": stopped, "trace": trace}


# ── Microsoft Graph (change notifications) ─────────────────────────────
def handle_graph_notifications(store, token_store, body: dict, *,
                               expected_client_state=None, provider_factory=None) -> dict:
    provider_factory = provider_factory or (lambda tok: get_provider("outlook", credentials=tok))
    stopped = 0
    for note in (body or {}).get("value", []):
        # clientState guards against forged notifications to our public URL.
        if expected_client_state is not None and \
                note.get("clientState") != expected_client_state:
            log.warning("graph notification with bad clientState ignored")
            continue
        res = note.get("resourceData") or {}
        msg_id = res.get("id") or note.get("resource")
        if not msg_id:
            continue
        if not store.mark_processed(f"graphpush:{msg_id}"):
            continue                       # duplicate delivery
        # Which user owns the subscription? Graph carries subscriptionId; we stored
        # it in watch_state. Fall back to scanning connected outlook accounts.
        sub_id = note.get("subscriptionId")
        for acct in _graph_accounts_for_subscription(token_store, sub_id):
            token = token_store.valid_access_token(acct["user_id"], "outlook",
                                                   acct["account_email"])
            if not token:
                continue
            try:
                msg = provider_factory(token).get_message(msg_id)
            except Exception as exc:       # noqa: BLE001
                metrics.incr("provider_failures")
                log.warning("graph get_message failed: %s", type(exc).__name__)
                continue
            if msg.get("from") and msg["from"] == acct["account_email"]:
                continue                   # our own outbound
            wf = engine.ingest_reply(store, message_id=msg.get("message_id"),
                                     user_id=acct["user_id"],
                                     thread_id=msg.get("thread_id"))
            if wf is not None and wf.state == "STOPPED":
                stopped += 1
    return {"ok": True, "stopped": stopped}


def _graph_accounts_for_subscription(token_store, sub_id):
    accts = token_store.with_watch("outlook")
    if sub_id:
        match = [a for a in accts
                 if (a.get("watch_state") or {}).get("subscription_id") == sub_id]
        if match:
            return match
    return accts


# ── Enabling notifications (called after connect / by the worker) ──────
def enable_gmail_watch(token_store, user_id, account_email, *, provider_factory=None):
    provider_factory = provider_factory or (lambda tok: get_provider("gmail", credentials=tok))
    token = token_store.valid_access_token(user_id, "gmail", account_email)
    if not token:
        return {"ok": False, "reason": "reconnect_required"}
    state = provider_factory(token).watch(user_id=user_id)
    token_store.set_watch_state(user_id, "gmail", account_email, state or {})
    return {"ok": True, "watch": state}
