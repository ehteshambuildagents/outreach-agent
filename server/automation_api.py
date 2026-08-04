"""Automation Agent HTTP surface — Clerk-gated, per-user, mounted on the app.

Thin transport over ``automation.engine``: create/list/get/cancel/pause/resume a
workflow, advance one on demand, expose metrics, and receive reply webhooks.
Every workflow route is scoped to the authenticated Clerk user, so no user can
see or touch another's automation. Content is never generated here — a workflow
carries emails the writer already produced.
"""

import logging
import os

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from automation import engine, health, tokens
from automation.metrics import snapshot as metrics_snapshot
from automation.store import WorkflowStore
from server.auth import require_user
from server.demo_auth import require_identity_or_demo
from server.demo_session import is_demo_id

log = logging.getLogger("saqua.automation_api")

_store = WorkflowStore()
_ALLOWED_PROVIDERS = {"dryrun", "gmail", "outlook"}
_MAX_STEPS = 10


def _scoped_metrics(user: str) -> dict:
    """Metrics derived ONLY from a single principal's own workflows.

    Used for the demo (and any per-user caller) so the response can never carry
    another user's data: the global process counters in ``automation.metrics`` are
    aggregate across every member, so a demo visitor must not see them. A demo
    principal owns no workflows (creation stays member-only), so this is zeros —
    but it is computed, not hard-coded, so it stays correct if that ever changes."""
    by_state: dict[str, int] = {}
    emails_sent = 0
    replies = 0
    for wf in _store.list_for_user(user):
        st = engine.status(wf)
        state = st.get("state", "")
        by_state[state] = by_state.get(state, 0) + 1
        emails_sent += int(st.get("current_step", 0) or 0)
        if st.get("reply_detected"):
            replies += 1
    reply_rate = round(replies / emails_sent, 4) if emails_sent else 0.0
    return {
        "metrics": {"emails_sent": emails_sent, "replies": replies,
                    "reply_rate": reply_rate},
        "by_state": by_state,
    }


class StepIn(BaseModel):
    subject: str
    body: str
    to: str = ""
    artifact_id: str = ""
    delay_days: int = 0

    @field_validator("subject", "body")
    @classmethod
    def _needed(cls, v):
        if not (v or "").strip():
            raise ValueError("subject and body are required")
        return v.strip()


class CreateWorkflow(BaseModel):
    to_email: str
    company: str = ""
    provider: str = "dryrun"
    conversation_id: str = None
    timezone: str = "UTC"
    steps: list = Field(default_factory=list)

    @field_validator("provider")
    @classmethod
    def _prov(cls, v):
        v = (v or "dryrun").lower()
        if v not in _ALLOWED_PROVIDERS:
            raise ValueError("unknown provider")
        return v

    @field_validator("to_email")
    @classmethod
    def _email(cls, v):
        v = (v or "").strip()
        if "@" not in v or len(v) > 254:
            raise ValueError("a valid recipient email is required")
        return v


def _steps_from_conversation(user: str, conversation_id: str) -> list:
    """Reuse the sequence/email the WRITER already produced for this user's thread.
    Never generates content."""
    from server.api import _store_for  # lazy: avoids an import cycle
    conv = _store_for(user).load(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    ws = conv.workspace or {}
    seq = ws.get("sequence")
    if seq:
        return [{"subject": e.get("subject"), "body": e.get("body"),
                 "delay_days": e.get("delay_days", 0),
                 "artifact_id": e.get("id") or e.get("artifact_id") or ""} for e in seq]
    email = ws.get("email")
    if email and email.get("status") == "ok":
        return [{"subject": email.get("subject"), "body": email.get("body"),
                 "delay_days": 0, "artifact_id": email.get("id", "")}]
    raise HTTPException(status_code=400,
                        detail="That conversation has no email or sequence to automate.")


def register(app, rl_read=None, rl_write=None):
    """Attach the automation routes to the given FastAPI app."""

    def _read(_request: Request):
        return rl_read(_request) if rl_read else None

    def _write(_request: Request):
        return rl_write(_request) if rl_write else None

    @app.post("/api/automation/workflows")
    def create_workflow(body: CreateWorkflow, request: Request,
                        _=Depends(_write), user: str = Depends(require_user)):
        steps = body.steps
        if not steps and body.conversation_id:
            steps = _steps_from_conversation(user, body.conversation_id)
        steps = [StepIn(**s).model_dump() if not isinstance(s, StepIn) else s.model_dump()
                 for s in (steps or [])][:_MAX_STEPS]
        if not steps:
            raise HTTPException(status_code=400, detail="No email steps to send.")
        try:
            wf = engine.create_workflow(
                _store, user, steps, company=body.company, to_email=body.to_email,
                provider=body.provider, conversation_id=body.conversation_id,
                timezone=body.timezone)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return engine.status(wf)

    @app.get("/api/automation/workflows")
    def list_workflows(request: Request, _=Depends(_read),
                       user: str = Depends(require_identity_or_demo)):
        # ``list_for_user`` filters by principal, so a demo visitor sees only its
        # own (empty) workflows and never another user's. Reads only; every
        # create/control route below stays member-only (``require_user``).
        return {"workflows": [engine.status(w) for w in _store.list_for_user(user)]}

    @app.get("/api/automation/workflows/{wid}")
    def get_workflow(wid: str, request: Request, _=Depends(_read),
                     user: str = Depends(require_user)):
        wf = _store.load(wid, user_id=user)
        if wf is None:
            raise HTTPException(status_code=404, detail="Workflow not found.")
        return {**engine.status(wf), "events": _store.events_for(wf.id)}

    def _control(action, wid, user):
        wf = getattr(engine, action)(_store, user, wid)
        if wf is None:
            raise HTTPException(status_code=404, detail="Workflow not found.")
        return engine.status(wf)

    @app.post("/api/automation/workflows/{wid}/cancel")
    def cancel_workflow(wid: str, request: Request, _=Depends(_write),
                        user: str = Depends(require_user)):
        return _control("cancel", wid, user)

    @app.post("/api/automation/workflows/{wid}/pause")
    def pause_workflow(wid: str, request: Request, _=Depends(_write),
                       user: str = Depends(require_user)):
        return _control("pause", wid, user)

    @app.post("/api/automation/workflows/{wid}/resume")
    def resume_workflow(wid: str, request: Request, _=Depends(_write),
                        user: str = Depends(require_user)):
        return _control("resume", wid, user)

    @app.post("/api/automation/workflows/{wid}/run")
    def run_workflow(wid: str, request: Request, _=Depends(_write),
                     user: str = Depends(require_user)):
        """Advance one owned workflow now (drives the state machine a step).
        A real deployment also runs a background worker calling engine.tick()."""
        wf = _store.load(wid, user_id=user)
        if wf is None:
            raise HTTPException(status_code=404, detail="Workflow not found.")
        engine.advance_workflow(wf, _store,
                                credentials_provider=tokens.credentials_provider)
        return engine.status(_store.load(wid, user_id=user))

    # ── admin recovery (owner-scoped) ──────────────────────────────────
    @app.get("/api/automation/dead-letter")
    def dead_letter(request: Request, _=Depends(_read),
                    user: str = Depends(require_user)):
        return {"workflows": [engine.status(w) for w in engine.dead_letter(_store, user)]}

    @app.post("/api/automation/workflows/{wid}/force-retry")
    def force_retry(wid: str, request: Request, _=Depends(_write),
                    user: str = Depends(require_user)):
        wf = engine.force_retry(_store, user, wid)
        if wf is None:
            raise HTTPException(status_code=404, detail="Workflow not found.")
        return engine.status(wf)

    @app.post("/api/automation/workflows/{wid}/force-complete")
    def force_complete(wid: str, request: Request, _=Depends(_write),
                       user: str = Depends(require_user)):
        wf = engine.force_complete(_store, user, wid)
        if wf is None:
            raise HTTPException(status_code=404, detail="Workflow not found.")
        return engine.status(wf)

    @app.get("/api/automation/metrics")
    def automation_metrics(request: Request, _=Depends(_read),
                           user: str = Depends(require_identity_or_demo)):
        # Members keep the existing global process snapshot (their real workspace,
        # unchanged). A demo visitor must NOT see those aggregate cross-user
        # counters, so it gets metrics scoped to its own workflows only. Branch on
        # the RESOLVED principal (demo_* namespace), not the request header, so the
        # rule holds regardless of how identity was established.
        if is_demo_id(user):
            return _scoped_metrics(user)
        return {"metrics": metrics_snapshot(), "by_state": _store.count_by_state()}

    @app.get("/api/automation/health")
    def automation_health(request: Request, _=Depends(_read),
                          user: str = Depends(require_user)):
        from automation.worker import _worker  # the running singleton, if any
        return health.snapshot(store=_store, worker=_worker)

    @app.post("/api/automation/reply-webhook")
    async def reply_webhook(request: Request):
        """Inbound reply notification (Gmail Pub/Sub / Graph). Idempotent.

        Gated by a shared secret because it can STOP a workflow. Provider
        signature verification would replace this in production."""
        secret = os.environ.get("AUTOMATION_WEBHOOK_SECRET", "")
        if not secret:
            raise HTTPException(status_code=503, detail="Webhook not configured.")
        if request.headers.get("X-Automation-Secret") != secret:
            raise HTTPException(status_code=401, detail="Bad webhook secret.")
        body = await request.json()
        wf = engine.ingest_reply(
            _store, message_id=str(body.get("message_id") or ""),
            workflow_id=body.get("workflow_id"), user_id=body.get("user_id"),
            thread_id=body.get("thread_id"))
        return {"ok": True, "stopped": bool(wf and wf.state == "STOPPED")}
