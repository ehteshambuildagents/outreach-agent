"""Campaign orchestration HTTP API.

This is a thin, durable orchestration layer over the existing agents. It does
not implement research/writing logic itself; it calls Discovery -> Research ->
Qualification -> Strategy -> Writer -> Guard, persists a preview, and only
launches automation workflows after an explicit user action.
"""

import concurrent.futures
import hashlib
import json
import logging
import threading
import time
from typing import Literal

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from agents import qualification, strategy, writer
from automation import engine
from automation.store import WorkflowStore
from config.settings import (
    AUTOMATION_MAX_FOLLOWUPS,
    AUTOMATION_REPLY_WAIT_DAYS,
    CAMPAIGN_PROSPECT_CONCURRENCY,
    CAMPAIGN_PROSPECT_DEADLINE_SECONDS,
)
from discovery.engine import discover
from discovery.models import DiscoveryQuery
from discovery.store import ProspectStore
from guard import assess as guard_assess
from guard.models import BLOCK
from research.pipeline import research_company
from server.auth import require_user
from server.demo_auth import require_identity_or_demo
from server.campaign_store import CampaignStore
from server import campaign_trail

_campaigns = None
_prospects = None
_workflows = None
log = logging.getLogger("saqua.campaign_api")

def explain_failure(exc: Exception, *, completed: str = "") -> str:
    """Turn an unexpected orchestration fault into something the user can act on.

    "Orchestration failed" tells a user nothing: not what worked, not what broke,
    not what to do. This names the SUBSYSTEM from the exception, states what had
    already succeeded, and gives the next step. It never leaks a stack trace or a
    connection string, only the class of fault.
    """
    name = type(exc).__name__
    text = f"{name} {exc}".lower()

    if any(k in text for k in ("timeout", "timed out", "deadline")):
        cause = ("the workflow service did not respond in time",
                 "Please retry in a moment; nothing was half-sent.")
    elif any(k in text for k in ("redis", "upstash")):
        cause = ("the coordination service (Redis) is temporarily unavailable",
                 "Scheduling needs it, so please retry shortly.")
    elif any(k in text for k in ("psycopg", "operationalerror", "database", "sqlite",
                                 "connection refused", "could not connect")):
        cause = ("the database could not be reached, so the campaign was not saved",
                 "Nothing was sent. Please retry in a moment.")
    elif any(k in text for k in ("mailbox", "no provider", "not connected", "oauth",
                                 "credentials", "token")):
        cause = ("no connected mailbox was available to schedule from",
                 "Connect a mailbox in Settings, then create the campaign again.")
    elif any(k in text for k in ("rate limit", "429", "quota", "capacity")):
        cause = ("a provider rate limit was hit",
                 "Please wait a minute and retry.")
    else:
        cause = (f"of an unexpected fault in the campaign service ({name})",
                 "It has been logged. Please retry; if it persists the campaign "
                 "service may be degraded.")

    done = (f"{completed} succeeded, but " if completed else "")
    return f"{done}the campaign could not be created because {cause[0]}. {cause[1]}"


_QUALIFIED = {qualification.CONTINUE, qualification.HIGH_PRIORITY}
_SEND_READY = {strategy.DRAFT, strategy.SEQUENCE}
_EMAIL_RE = __import__("re").compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class CampaignICP(BaseModel):
    raw: str = ""
    industry: str = ""
    location: str = ""
    employee_range: str = ""
    funding_stage: str = ""
    keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)


class CampaignCreate(BaseModel):
    name: str = "New campaign"
    icp: CampaignICP = Field(default_factory=CampaignICP)
    limit: int = 5
    idempotency_key: str | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = " ".join((value or "").replace("\x00", "").split()).strip()
        if not value:
            raise ValueError("Campaign name is required.")
        return value[:80]

    @field_validator("limit")
    @classmethod
    def _limit(cls, value: int) -> int:
        return max(1, min(int(value or 5), 10))


class LaunchCampaign(BaseModel):
    provider: Literal["dryrun", "gmail", "outlook"] = "dryrun"
    timezone: str = "UTC"


class ManualRecipientUpdate(BaseModel):
    name: str = ""
    email: str

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        return " ".join((value or "").replace("\x00", "").split()).strip()[:120]

    @field_validator("email")
    @classmethod
    def _clean_email(cls, value: str) -> str:
        return (value or "").replace("\x00", "").strip().lower()[:254]


def register(app, rl_read=None, rl_write=None):
    def _read(request: Request):
        return rl_read(request) if rl_read else None

    def _write(request: Request):
        return rl_write(request) if rl_write else None

    # Create / list / get are identity-or-demo: a sandboxed demo visitor runs the
    # SAME research + drafting pipeline as a member, scoped to their own demo
    # principal, and everything stays drafts-only. Research cost is capped per
    # principal at the provider call site (research/providers_common), exactly as
    # the demo chat already is. Only launch/pause/resume/cancel below stay
    # member-only (require_user) — those move real mail, which the demo never does.
    # (list_prospects was already demo-aware; create/list/get had been missed,
    # leaving every demo visitor a 401 on the Campaigns and New Campaign pages.)
    @app.post("/api/campaigns")
    def create_campaign(body: CampaignCreate, request: Request,
                        _=Depends(_write), user: str = Depends(require_identity_or_demo)):
        key = body.idempotency_key or request.headers.get("Idempotency-Key") or _request_hash(user, body)
        trace_id = (request.headers.get("x-request-id") or request.headers.get("x-saqua-proxy-trace-id") or key[:12]).strip()
        user_hash = hashlib.sha256(user.encode("utf-8")).hexdigest()[:12]
        log.info(
            "campaign_create_start trace_id=%s user_hash=%s name=%r limit=%s raw_len=%s",
            trace_id, user_hash, body.name, body.limit, len(body.icp.raw or ""),
        )
        # What had demonstrably worked when a fault hit, so the error can say so
        # instead of implying the whole run was wasted.
        completed = ""
        try:
            campaigns = _campaign_store()
            existing = campaigns.get_by_idempotency_key(user, key)
            if existing:
                log.info("campaign_create_idempotent trace_id=%s campaign_id=%s", trace_id, existing["id"])
                return _campaign_public(existing, idempotent=True)

            result, status, events = _run_pipeline(user, body, trace_id=trace_id)
            completed = "Research and drafting"
            log.info("campaign_stage_start trace_id=%s stage=persist status=%s", trace_id, status)
            try:
                campaign = campaigns.create(
                    owner=user, idempotency_key=key, name=body.name,
                    request=body.model_dump(), result=result, status=status,
                )
            except Exception:  # noqa: BLE001 - most likely the UNIQUE(owner, key) constraint
                # A concurrent request with the same idempotency key (e.g. a client
                # retry after a slow response) won the INSERT first. That is a
                # successful de-duplication, not a fault: return their campaign
                # rather than a 500. Re-raise only if no such row actually exists.
                existing = campaigns.get_by_idempotency_key(user, key)
                if existing is None:
                    raise
                log.info("campaign_create_idempotent_after_conflict trace_id=%s campaign_id=%s",
                         trace_id, existing["id"])
                return _campaign_public(existing, idempotent=True)
            for event in events:
                try:
                    campaigns.add_event(user, campaign["id"], event["type"], event["detail"])
                except Exception:  # noqa: BLE001 - events are useful, not response-critical
                    log.exception("campaign_event_persist_failed trace_id=%s campaign_id=%s", trace_id, campaign["id"])
            log.info(
                "campaign_stage_complete trace_id=%s stage=persist campaign_id=%s status=%s",
                trace_id, campaign["id"], status,
            )
            log.info(
                "campaign_create_complete trace_id=%s campaign_id=%s status=%s launchable=%s",
                trace_id, campaign["id"], status, (result.get("summary") or {}).get("launchable"),
            )
            return _campaign_public(campaigns.get(user, campaign["id"]))
        except HTTPException:
            raise
        except Exception as exc:
            # The pipeline itself never raises for provider/stage failures (those become
            # clean statuses). Reaching here means an unexpected server fault (e.g. the
            # datastore is unreachable) — return an *explained*, traceable error, never a
            # bare 500, so the UI can show something actionable.
            log.exception("campaign_create_failed trace_id=%s user_hash=%s error=%s",
                          trace_id, user_hash, type(exc).__name__)
            raise HTTPException(
                status_code=500,
                detail=(explain_failure(exc, completed=completed)
                        + f" (reference {trace_id})"),
            ) from exc

    @app.get("/api/campaigns")
    def list_campaigns(request: Request, _=Depends(_read),
                       user: str = Depends(require_identity_or_demo)):
        rows = _campaign_store().list_for_owner(user)
        return {"campaigns": [_campaign_summary(row) for row in rows]}

    @app.get("/api/campaigns/{campaign_id}")
    def get_campaign(campaign_id: str, request: Request, _=Depends(_read),
                     user: str = Depends(require_identity_or_demo)):
        campaign = _campaign_store().get(user, campaign_id)
        if campaign is None:
            raise HTTPException(status_code=404, detail="Campaign not found.")
        return _campaign_public(campaign)

    @app.get("/api/prospects")
    def list_prospects(request: Request, _=Depends(_read),
                       user: str = Depends(require_identity_or_demo)):
        # Demo principals resolve to their own (empty) prospect store, so a demo
        # visitor sees the real page with the real empty state — never a member's.
        return {"prospects": [_prospect_public_with_trail(p.public())
                              for p in _prospect_store().list_for_owner(user)]}

    @app.post("/api/campaigns/{campaign_id}/prospects/{domain}/recipient")
    def update_prospect_recipient(campaign_id: str, domain: str, body: ManualRecipientUpdate,
                                  request: Request, _=Depends(_write),
                                  user: str = Depends(require_user)):
        campaigns = _campaign_store()
        campaign = campaigns.get(user, campaign_id)
        if campaign is None:
            raise HTTPException(status_code=404, detail="Campaign not found.")
        if campaign["status"] == "launched":
            raise HTTPException(status_code=400, detail="Launched campaigns cannot be edited.")

        email_error = _manual_email_error(body.email)
        if email_error:
            raise HTTPException(status_code=422, detail=email_error)

        result, changed, guard_allowed = _apply_manual_recipient(campaign["result"], domain, body)
        if not changed:
            raise HTTPException(status_code=404, detail="Prospect not found in this campaign.")

        status = "ready" if (result.get("summary") or {}).get("launchable", 0) > 0 else "blocked"
        updated = campaigns.update(user, campaign_id, status=status, result=result)
        campaigns.add_event(
            user,
            campaign_id,
            "recipient_updated",
            f"{domain}: manual recipient saved; guard {'allowed' if guard_allowed else 'blocked'}",
        )
        return _campaign_public(updated)

    @app.post("/api/campaigns/{campaign_id}/launch")
    def launch_campaign(campaign_id: str, body: LaunchCampaign, request: Request,
                        _=Depends(_write), user: str = Depends(require_user)):
        campaigns = _campaign_store()
        campaign = campaigns.get(user, campaign_id)
        if campaign is None:
            raise HTTPException(status_code=404, detail="Campaign not found.")
        # Fast path: re-launching a campaign that already has workflows is a no-op.
        if campaign["status"] == "launched" and campaign["workflow_ids"]:
            return _campaign_public(campaign)

        prelaunch_status = campaign["status"]
        duplicate_recipients = []
        launchable = _launchable_emails(campaign["result"],
                                        duplicates=duplicate_recipients)
        if not launchable:
            raise HTTPException(status_code=400, detail="No guard-approved emails with valid recipient addresses.")

        # Reserve the launch atomically so a double-submit cannot double-send. Only
        # the winner creates workflows; a loser returns the authoritative row as-is,
        # having sent nothing. Before this guard two concurrent launches (a double
        # click, or a client retry after a slow response) each built a full workflow
        # set — every prospect received the sequence twice, and the campaign tracked
        # only one of the two, orphaning the other beyond pause/cancel.
        if not campaigns.claim_launch(user, campaign_id):
            current = campaigns.get(user, campaign_id)
            return _campaign_public(current if current is not None else campaign)

        try:
            workflow_ids = []
            for item in launchable:
                wf = engine.create_workflow(
                    _workflow_store(), user, _cadence_steps(item),
                    company=item["company"], to_email=item["to"], provider=body.provider,
                    conversation_id=campaign_id, timezone=body.timezone,
                )
                workflow_ids.append(wf.id)
            result = dict(campaign["result"])
            result["launched_at"] = time.time()
            result["workflow_ids"] = workflow_ids
            # Flag any prospect that launched with a 3-step (no-follow-up) cadence so the
            # UI can warn — nobody should ship a silently incomplete sequence unknowingly.
            warnings = [{"domain": p.get("domain") or p.get("website"), **w}
                        for p in (result.get("prospects") or [])
                        if (w := _cadence_warning(p))]
            if warnings:
                result["launched_with_warnings"] = warnings
            # Prospects that share a recipient with an earlier one were collapsed
            # rather than sent twice. Report it: from the user's side a prospect
            # they previewed did not launch, and they are owed the reason.
            if duplicate_recipients:
                result["duplicate_recipients_skipped"] = duplicate_recipients
            updated = campaigns.update(user, campaign_id, status="launched",
                                       result=result, workflow_ids=workflow_ids)
            campaigns.add_event(user, campaign_id, "launched",
                                f"{len(workflow_ids)} workflow(s) created via {body.provider}")
            if warnings:
                campaigns.add_event(user, campaign_id, "launched_incomplete",
                                    f"{len(warnings)} prospect(s) launched with a 3-step cadence")
            if duplicate_recipients:
                campaigns.add_event(
                    user, campaign_id, "duplicate_recipients_skipped",
                    f"{len(duplicate_recipients)} prospect(s) shared a recipient "
                    "address with another and were not sent a second sequence")
            return _campaign_public(updated)
        except Exception:
            # We hold the claim (status is 'launching') but did not finish. Release it
            # back to the pre-launch status so the campaign is not wedged; a retry can
            # then launch cleanly, and stale-claim recovery is the backstop if even
            # this release fails. Workflows already created before the fault are not
            # re-persisted here — that rare partial-launch case is the known edge that
            # idempotent workflow creation (the deferred follow-up) would close.
            try:
                campaigns.update(user, campaign_id, status=prelaunch_status)
            except Exception:  # noqa: BLE001 - best effort; stale-claim recovery still applies
                log.exception("launch_claim_release_failed campaign_id=%s", campaign_id)
            raise

    @app.post("/api/campaigns/{campaign_id}/pause")
    def pause_campaign(campaign_id: str, request: Request, _=Depends(_write),
                       user: str = Depends(require_user)):
        return _control_campaign(user, campaign_id, "pause")

    @app.post("/api/campaigns/{campaign_id}/resume")
    def resume_campaign(campaign_id: str, request: Request, _=Depends(_write),
                        user: str = Depends(require_user)):
        return _control_campaign(user, campaign_id, "resume")

    @app.post("/api/campaigns/{campaign_id}/cancel")
    def cancel_campaign(campaign_id: str, request: Request, _=Depends(_write),
                        user: str = Depends(require_user)):
        return _control_campaign(user, campaign_id, "cancel")


def _run_pipeline(user: str, body: CampaignCreate, trace_id: str = "") -> tuple[dict, str, list[dict]]:
    events = []
    events_lock = threading.Lock()

    def event(type_: str, detail: str) -> None:
        # Prospects now run concurrently, so this is called from several threads.
        with events_lock:
            events.append({"type": type_, "detail": detail})

    started = time.time()
    event("started", f"Campaign preview started for {body.name}")
    query = _discovery_query(body)
    log.info("campaign_stage_start trace_id=%s stage=discovery limit=%s", trace_id, body.limit)
    try:
        discovery = discover(user, query, store=_prospect_store())
    except Exception as exc:  # noqa: BLE001 - a provider error/timeout must not 500 the campaign
        log.exception("campaign_stage_failed trace_id=%s stage=discovery", trace_id)
        event("discovery_error", f"discovery failed: {type(exc).__name__}")
        reason = ("Prospect discovery could not complete: a data provider errored or timed out, "
                  "so no prospects were processed. Please try again in a moment.")
        result = {
            "status": "provider_failed",
            "reason": reason,
            "summary": _summary([], started),
            "prospects": [],
            "discovery": {"status": "error", "returned": 0, "reason": reason},
        }
        log.info("campaign_result trace_id=%s status=provider_failed stage=discovery", trace_id)
        return result, "blocked", events
    log.info(
        "campaign_stage_complete trace_id=%s stage=discovery status=%s returned=%s",
        trace_id, discovery.status, discovery.returned,
    )
    event("discovery", f"{discovery.status}: {discovery.returned} prospect(s)")

    if discovery.status != "ok":
        result_status = "no_valid_prospects" if discovery.status == "empty" else "discovery_error"
        reason = discovery.reason or "Prospect discovery did not return usable companies."
        result = {
            "status": result_status,
            "reason": reason,
            "summary": _summary([], started),
            "prospects": [],
            "discovery": discovery.public(),
        }
        log.info(
            "campaign_result trace_id=%s status=%s discovery_status=%s reason=%r",
            trace_id, result_status, discovery.status, reason,
        )
        return result, "blocked", events

    prospects = _process_prospects(user, discovery.prospects, body.icp.model_dump(),
                                   event, trace_id=trace_id)

    summary = _summary(prospects, started)
    if summary["launchable"] > 0:
        result_status = "ready"
        status = "ready"
        reason = None
    elif summary["discovered"] > 0 and summary["research_ok"] == 0:
        result_status = "needs_more_input"
        status = "blocked"
        reason = "No launchable prospects found because research failed for every discovered company."
    else:
        result_status = "no_launchable_prospects"
        status = "blocked"
        reason = ("No launchable prospects: every email was blocked, held, route-only, or lacked a "
                  "valid recipient address, so nothing can be sent.")
    result = {
        "status": result_status,
        "reason": reason,
        "summary": summary,
        "prospects": prospects,
        "discovery": discovery.public(),
    }
    log.info(
        "campaign_result trace_id=%s status=%s launchable=%s research_ok=%s guard_blocked=%s",
        trace_id, result_status, summary["launchable"], summary["research_ok"], summary["guard_blocked"],
    )
    event("completed", f"{summary['launchable']} launchable prospect(s), {summary['guard_blocked']} blocked")
    return result, status, events


def _process_prospects(user: str, discovered: list, icp: dict, event,
                       *, trace_id: str = "") -> list[dict]:
    """Research, qualify, write and guard every prospect, several at a time.

    Sequential processing was the whole cost of a campaign: each prospect is a
    separate site and a separate draft, sharing nothing but the stores (which
    already open one connection per thread), and nearly all of the time is spent
    waiting on someone else's network. So the run was idle almost end to end.

    Two guarantees make this safe to keep inside the HTTP request:

    * ORDER is preserved. Results are placed by index, so the response still
      reads in discovery's ranked order no matter what finishes first.
    * The phase is BOUNDED. One unreachable host can exhaust every fetch retry;
      previously that spent the request's whole budget and the platform killed
      it with nothing saved. Now the campaign keeps whatever finished and says
      plainly which prospects ran out of time, which is strictly more than a
      dead request returned.
    """
    if not discovered:
        return []
    workers = max(1, min(CAMPAIGN_PROSPECT_CONCURRENCY, len(discovered)))
    results: list = [None] * len(discovered)
    deadline = time.time() + CAMPAIGN_PROSPECT_DEADLINE_SECONDS
    log.info("campaign_stage_start trace_id=%s stage=prospects n=%d workers=%d",
             trace_id, len(discovered), workers)

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers,
                                                 thread_name_prefix="campaign")
    try:
        futures = {
            pool.submit(_process_prospect_safely, user, p, icp, event,
                        trace_id=trace_id): i
            for i, p in enumerate(discovered)
        }
        pending = set(futures)
        while pending:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            done, pending = concurrent.futures.wait(
                pending, timeout=remaining,
                return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                results[futures[future]] = future.result()
        for future in pending:
            future.cancel()
            index = futures[future]
            results[index] = _out_of_time(discovered[index])
            log.info("campaign_stage_timeout trace_id=%s stage=prospects domain=%s",
                     trace_id, getattr(discovered[index], "domain", ""))
            event("prospect_timeout",
                  f"{getattr(discovered[index], 'domain', '')}: ran out of time")
    finally:
        # Never block on threads whose results are already written off: the
        # context-manager form waits for them, which would hand the entire
        # saving back to the slow prospect we just gave up on.
        pool.shutdown(wait=False, cancel_futures=True)
    return results


def _process_prospect_safely(user: str, prospect, icp: dict, event,
                             *, trace_id: str = "") -> dict:
    """_process_prospect, but a raise becomes a result. Sequentially a stray
    exception failed one prospect; through a pool it would fail the campaign."""
    try:
        return _process_prospect(user, prospect, icp, event, trace_id=trace_id)
    except Exception:  # noqa: BLE001 - one bad prospect must not end the run
        log.exception("campaign_prospect_crashed trace_id=%s domain=%s",
                      trace_id, getattr(prospect, "domain", ""))
        return {"domain": getattr(prospect, "domain", ""),
                "company_name": getattr(prospect, "company_name", ""),
                "final_status": "research_failed",
                "reason": "This lead failed unexpectedly and was skipped; "
                          "the rest of the campaign is unaffected."}


def _out_of_time(prospect) -> dict:
    """A prospect the deadline reached first. Says what happened rather than
    reporting it as a research failure, which would blame the company's site.

    Defensive on purpose: this runs on the path where the run is already in
    trouble, and a raise here would lose every prospect that DID finish — the
    exact outcome the deadline exists to prevent.
    """
    try:
        base = dict(prospect.public())
    except Exception:  # noqa: BLE001
        base = {"company_name": getattr(prospect, "company_name", "")}
    base.update({
        "domain": getattr(prospect, "domain", ""),
        "final_status": "timed_out",
        "reason": "This company was still being researched when the run hit its "
                  "time limit, so it was left out rather than rushed. Run the "
                  "campaign again to pick it up.",
    })
    return base


def _process_prospect(user: str, prospect, icp: dict, event, trace_id: str = "") -> dict:
    base = prospect.public()
    base.update({"domain": prospect.domain, "final_status": "research_failed"})

    log.info("campaign_stage_start trace_id=%s stage=research domain=%s", trace_id, prospect.domain)
    try:
        research = research_company(prospect.website, find_founder=True)
    except Exception:  # noqa: BLE001 - one bad prospect must not crash campaign
        log.exception("campaign_stage_failed trace_id=%s stage=research domain=%s", trace_id, prospect.domain)
        base["final_status"] = "research_failed"
        base["reason"] = "Research failed before usable company evidence could be produced."
        return base

    base["research"] = _research_public(research)
    log.info(
        "campaign_stage_complete trace_id=%s stage=research domain=%s status=%s",
        trace_id, prospect.domain, research.get("status"),
    )
    event("research", f"{prospect.domain}: {research.get('status')}")
    if research.get("status") != "ok":
        base["final_status"] = "research_failed"
        base["reason"] = research.get("reason") or research.get("error") or "Research failed."
        return base

    try:
        _prospect_store().mark_researched(user, prospect.domain)
    except Exception:  # noqa: BLE001 - non-critical marker
        log.exception("campaign_stage_failed trace_id=%s stage=research_persist domain=%s", trace_id, prospect.domain)

    log.info("campaign_stage_start trace_id=%s stage=qualification domain=%s", trace_id, prospect.domain)
    try:
        q = qualification.qualify(research=research, icp=icp).to_dict()
    except Exception:  # noqa: BLE001
        log.exception("campaign_stage_failed trace_id=%s stage=qualification domain=%s", trace_id, prospect.domain)
        base["final_status"] = "qualification_blocked"
        base["reason"] = "Qualification failed, so this lead was not allowed to continue."
        return base

    log.info(
        "campaign_stage_complete trace_id=%s stage=qualification domain=%s recommendation=%s score=%s",
        trace_id, prospect.domain, q.get("recommendation"), q.get("qualification_score"),
    )
    base["qualification"] = q
    if q["recommendation"] not in _QUALIFIED:
        base["final_status"] = "qualification_blocked"
        base["reason"] = q.get("next_best_action") or "Qualification blocked outreach."
        return base

    log.info("campaign_stage_start trace_id=%s stage=strategy domain=%s", trace_id, prospect.domain)
    try:
        s = strategy.decide(research=research, qualification=q).to_dict()
    except Exception:  # noqa: BLE001
        log.exception("campaign_stage_failed trace_id=%s stage=strategy domain=%s", trace_id, prospect.domain)
        base["final_status"] = "strategy_hold"
        base["reason"] = "Strategy failed, so this lead was held before writing."
        return base

    log.info(
        "campaign_stage_complete trace_id=%s stage=strategy domain=%s action=%s",
        trace_id, prospect.domain, s.get("recommended_action"),
    )
    base["strategy"] = s
    if s["recommended_action"] not in _SEND_READY:
        base["final_status"] = "strategy_hold"
        base["reason"] = s.get("reasoning_summary") or "Strategy did not approve outreach."
        return base

    log.info("campaign_stage_start trace_id=%s stage=writer domain=%s", trace_id, prospect.domain)
    try:
        email = writer.write_email({**research, "qualification": q, "strategy": s})
    except Exception:  # noqa: BLE001
        log.exception("campaign_stage_failed trace_id=%s stage=writer domain=%s", trace_id, prospect.domain)
        base["final_status"] = "writer_failed"
        base["reason"] = "Writer failed to produce a usable email for this lead."
        return base

    log.info(
        "campaign_stage_complete trace_id=%s stage=writer domain=%s status=%s",
        trace_id, prospect.domain, email.get("status"),
    )
    base["email"] = _email_public(email, research)
    if email.get("status") != "ok":
        base["final_status"] = "writer_failed"
        base["reason"] = email.get("reason") or "Writer did not produce a sendable email."
        return base

    log.info("campaign_stage_start trace_id=%s stage=guard domain=%s", trace_id, prospect.domain)
    try:
        guard_input = _guard_input(research, q, s, email)
        guard = guard_assess(guard_input)
    except Exception:  # noqa: BLE001
        log.exception("campaign_stage_failed trace_id=%s stage=guard domain=%s", trace_id, prospect.domain)
        base["final_status"] = "guard_blocked"
        base["reason"] = "Guard failed closed, so this email was blocked."
        base["guard"] = {
            "decision": BLOCK,
            "overallRisk": 100,
            "deliverability": {"risk": "CRITICAL", "issues": ["Guard failed closed."], "recommendations": []},
            "cost": {"risk": "LOW", "issues": [], "recommendations": []},
        }
        return base

    log.info(
        "campaign_stage_complete trace_id=%s stage=guard domain=%s decision=%s",
        trace_id, prospect.domain, guard.get("decision"),
    )
    base["guard"] = guard
    if guard.get("decision") == BLOCK:
        base["final_status"] = "guard_blocked"
        base["reason"] = _guard_reason(guard)
        return base

    base["final_status"] = "sendable" if _valid_email(_recipient_email(research)) else "route_only"
    base["recipient"] = _recipient(research)
    base["reason"] = None
    if base["final_status"] == "sendable":
        # Generate the two mid-cadence follow-ups NOW, while the full research is in
        # hand — it is not persisted past this function. Slots 1 (verbatim bump) and
        # 4 (breakup) are deterministic and assembled at launch; slots 2 & 3 run
        # through the SAME writer + guard pipeline the original email just did.
        base["followups"] = _generate_followups(research, q, s, email,
                                                 trace_id=trace_id, domain=prospect.domain)
    return base


# ── Follow-up cadence (generation happens here, at campaign-create time) ──
def _generate_followups(research: dict, q: dict, s: dict, email: dict, *,
                        trace_id: str = "", domain: str = "") -> list:
    """Writer + guard for the two mid-cadence follow-ups (slots 2 & 3), chained so
    the second builds on the first — a real progression, not a reworded copy. A
    guard BLOCK triggers one regeneration, then the touch is dropped. Returns the
    survivors as persisted content dicts; a prospect may end up with 0, 1, or 2."""
    out = []
    previous = email                      # slot 2 builds on the approved original
    for slot in (2, 3):
        fu = _followup_with_guard(research, q, s, previous, trace_id=trace_id, domain=domain)
        if fu is None:
            continue                      # dropped; slot 3 still builds on `previous`
        out.append({"slot": slot, "subject": fu["subject"], "body": fu["body"],
                    "guard_decision": fu["guard_decision"],
                    "delay_days": AUTOMATION_REPLY_WAIT_DAYS})
        previous = fu                     # chain the next follow-up onto this one
    return out


def _followup_with_guard(research: dict, q: dict, s: dict, previous_email: dict, *,
                         trace_id: str = "", domain: str = ""):
    """One follow-up through writer + guard, with a single retry on BLOCK (or on a
    writer/guard failure). Returns the accepted follow-up plus its guard decision,
    or None if it never cleared and the touch should be dropped."""
    for attempt in (1, 2):
        try:
            fu = writer.write_followup(research, previous_email)
        except Exception:  # noqa: BLE001 - a writer failure just drops the touch
            log.exception("campaign_followup_writer_failed trace_id=%s domain=%s attempt=%s",
                          trace_id, domain, attempt)
            continue
        if fu.get("status") != "ok" or not (fu.get("subject") and fu.get("body")):
            continue
        try:
            guard = guard_assess(_guard_input(research, q, s, fu))
        except Exception:  # noqa: BLE001 - guard fails closed: treat as blocked, retry then drop
            log.exception("campaign_followup_guard_failed trace_id=%s domain=%s attempt=%s",
                          trace_id, domain, attempt)
            continue
        if guard.get("decision") != BLOCK:
            return {**fu, "guard_decision": guard.get("decision")}
        log.info("campaign_followup_blocked trace_id=%s domain=%s attempt=%s",
                 trace_id, domain, attempt)
    return None


def _discovery_query(body: CampaignCreate) -> DiscoveryQuery:
    icp = body.icp
    return DiscoveryQuery(
        raw=icp.raw,
        industry=icp.industry,
        location=icp.location,
        employee_range=icp.employee_range,
        funding_stage=icp.funding_stage,
        keywords=icp.keywords,
        exclude_keywords=icp.exclude_keywords,
        limit=body.limit,
    )


def _request_hash(user: str, body: CampaignCreate) -> str:
    payload = json.dumps(body.model_dump(), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256((user + ":" + payload).encode("utf-8")).hexdigest()


def _research_public(research: dict) -> dict:
    data = (research or {}).get("data") or {}
    return {
        "status": (research or {}).get("status"),
        "reason": (research or {}).get("reason") or (research or {}).get("error"),
        "company_name": data.get("company_name"),
        "summary": data.get("what_they_do"),
        "pages_crawled": (research or {}).get("pages_crawled") or [],
        "research_score": (research or {}).get("research_score"),
        "named_person": data.get("primary_contact_name") or data.get("founder_name"),
        "recipient": _recipient(research),
        "hooks": [h.get("text") for h in ((research or {}).get("hooks") or []) if h.get("text")][:6],
    }


def _recipient(research: dict) -> dict:
    data = (research or {}).get("data") or {}
    return {
        "name": data.get("primary_contact_name") or data.get("founder_name"),
        "role": data.get("primary_contact_role") or data.get("founder_role"),
        "email": _recipient_email(research),
        "linkedin": data.get("linkedin_url"),
        "contact_page": data.get("contact_page_url"),
        "route": data.get("recipient_route"),
    }


def _recipient_email(research: dict) -> str:
    data = (research or {}).get("data") or {}
    return (data.get("public_contact_email") or "").strip().lower()


def _email_public(email: dict, research: dict) -> dict:
    recipient = _recipient(research)
    return {
        "status": email.get("status"),
        "reason": email.get("reason"),
        "subject": email.get("subject"),
        "body": email.get("body"),
        "to": recipient["email"] or email.get("to"),
        "recipient": recipient,
        "quality": email.get("quality"),
    }


def _guard_input(research: dict, q: dict, s: dict, email: dict) -> dict:
    data = research.get("data") or {}
    recipient = _recipient(research)
    return {
        "company": data.get("company_name"),
        "prospect": {"company": data.get("company_name")},
        "research": data,
        "qualification": q,
        "strategy": s,
        "writer": {"status": email.get("status")},
        "email": {
            "subject": email.get("subject"),
            "body": email.get("body"),
            "to": recipient["email"] or recipient["route"],
            "company": data.get("company_name"),
        },
        "personalization": {
            "based_on_research": True,
            "specific": bool(data.get("unique_hook") or data.get("additional_hooks")),
            "generic": False,
            "fabricated": False,
        },
        "campaign": {"recipients": 1, "ai_calls": 1},
    }


def _guard_input_for_manual_recipient(prospect: dict, recipient: dict) -> dict:
    research = prospect.get("research") or {}
    email = prospect.get("email") or {}
    company = prospect.get("company_name") or research.get("company_name") or prospect.get("domain")
    return {
        "company": company,
        "prospect": {"company": company},
        "research": {
            "company_name": company,
            "what_they_do": research.get("summary"),
            "hooks": research.get("hooks") or [],
            "manual_recipient": recipient,
        },
        "qualification": prospect.get("qualification") or {},
        "strategy": prospect.get("strategy") or {},
        "writer": {"status": email.get("status")},
        "email": {
            "subject": email.get("subject"),
            "body": email.get("body"),
            "to": recipient["email"],
            "company": company,
        },
        "personalization": {
            "based_on_research": True,
            "specific": bool((research.get("hooks") or []) or research.get("summary")),
            "generic": False,
            "fabricated": False,
        },
        "campaign": {"recipients": 1, "ai_calls": 1},
    }


def _apply_manual_recipient(result: dict, domain: str, body: ManualRecipientUpdate) -> tuple[dict, bool, bool]:
    updated = dict(result or {})
    prospects = [dict(p) for p in (updated.get("prospects") or [])]
    target = _normalize_domain(domain)
    changed = False
    guard_allowed = False

    for i, prospect in enumerate(prospects):
        if _normalize_domain(prospect.get("domain") or prospect.get("website") or "") != target:
            continue
        changed = True
        email = dict(prospect.get("email") or {})
        if email.get("status") != "ok" or not (email.get("subject") and email.get("body")):
            raise HTTPException(status_code=400, detail="This prospect has no generated email to update.")

        recipient = dict(email.get("recipient") or prospect.get("recipient") or {})
        recipient.update({
            "name": body.name or recipient.get("name"),
            "email": body.email,
            "manual": True,
        })
        email["to"] = body.email
        email["recipient"] = recipient
        prospect["recipient"] = recipient
        prospect["email"] = email
        prospect["manual_recipient"] = {"name": recipient.get("name"), "email": body.email}

        try:
            guard = guard_assess(_guard_input_for_manual_recipient(prospect, recipient))
        except Exception:  # noqa: BLE001 - manual recipient updates must fail closed
            log.exception("manual_recipient_guard_failed domain=%s", prospect.get("domain"))
            guard = {
                "decision": BLOCK,
                "overallRisk": 100,
                "deliverability": {
                    "risk": "CRITICAL",
                    "issues": ["Guard re-check failed."],
                    "recommendations": [],
                },
                "cost": {"risk": "LOW", "issues": [], "recommendations": []},
            }
        prospect["guard"] = guard
        guard_allowed = guard.get("decision") == "ALLOW"
        if guard_allowed:
            prospect["final_status"] = "sendable"
            prospect["reason"] = None
        else:
            prospect["final_status"] = "guard_blocked"
            prospect["reason"] = _guard_reason(guard)
        prospects[i] = prospect
        break

    if not changed:
        return updated, False, False

    updated["prospects"] = prospects
    updated["summary"] = _summary_from_prospects(prospects, updated.get("summary") or {})
    if updated["summary"]["launchable"] > 0:
        updated["status"] = "ready"
        updated["reason"] = None
    else:
        updated["status"] = "no_launchable_prospects"
        updated["reason"] = "No launchable prospects have a valid guard-approved recipient address yet."
    return updated, True, guard_allowed


_PLACEHOLDER_DOMAINS = {
    "example.com", "example.org", "example.net", "test.com", "test.co",
    "domain.com", "company.com", "yourcompany.com", "localhost",
}
_PLACEHOLDER_LOCALS = {
    "founder", "ceo", "owner", "name", "email", "person", "user", "test",
    "example", "john", "jane", "john.doe", "jane.doe", "first", "last",
    "first.last", "firstname", "lastname", "first_name", "last_name",
}


def _manual_email_error(value: str) -> str | None:
    email = (value or "").strip().lower()
    if not _valid_email(email):
        return "Enter a valid recipient email address."
    local, _, domain = email.partition("@")
    if domain in _PLACEHOLDER_DOMAINS or domain.endswith(".example"):
        return "Placeholder email domains are not allowed."
    normalized_local = local.replace("+", ".").strip(".")
    if normalized_local in _PLACEHOLDER_LOCALS:
        return "Guessed placeholder email addresses are not allowed."
    if "{{" in email or "}}" in email or "[" in email or "]" in email:
        return "Template email placeholders are not allowed."
    return None


def _normalize_domain(value: str) -> str:
    value = (value or "").strip().lower()
    value = value.removeprefix("https://").removeprefix("http://").removeprefix("www.")
    return value.split("/", 1)[0]


def _guard_reason(guard: dict) -> str:
    issues = (guard.get("deliverability", {}).get("issues") or []) + (guard.get("cost", {}).get("issues") or [])
    return "; ".join(issues[:3]) or "Guard blocked this email."


def _summary(prospects: list[dict], started: float) -> dict:
    counts = _summary_from_prospects(prospects)
    counts["estimated_cost"] = round(counts["discovered"] * 0.07, 4)
    counts["latency_seconds"] = round(time.time() - started, 2)
    return counts


def _summary_from_prospects(prospects: list[dict], previous: dict | None = None) -> dict:
    counts = {name: 0 for name in (
        "discovered", "research_ok", "qualified", "emails_generated",
        "guard_allowed", "guard_blocked", "launchable", "route_only")}
    counts["discovered"] = len(prospects)
    for item in prospects:
        if item.get("research", {}).get("status") == "ok":
            counts["research_ok"] += 1
        if item.get("qualification", {}).get("recommendation") in _QUALIFIED:
            counts["qualified"] += 1
        if item.get("email", {}).get("status") == "ok":
            counts["emails_generated"] += 1
        if item.get("guard", {}).get("decision") == "ALLOW":
            counts["guard_allowed"] += 1
        if item.get("final_status") == "guard_blocked":
            counts["guard_blocked"] += 1
        if item.get("final_status") == "sendable":
            counts["launchable"] += 1
        if item.get("final_status") == "route_only":
            counts["route_only"] += 1
    if previous:
        counts["estimated_cost"] = previous.get("estimated_cost", round(counts["discovered"] * 0.07, 4))
        counts["latency_seconds"] = previous.get("latency_seconds", 0)
    return counts


def _launchable_emails(result: dict, *, duplicates: list = None) -> list[dict]:
    """The guard-approved emails to actually launch, AT MOST ONE PER RECIPIENT.

    Two prospects in one campaign can legitimately resolve to the same address:
    research falls back to a generic ``info@``/``hello@`` inbox, or a parent and
    its subsidiary share one. Each launched prospect becomes its own 5-touch
    workflow, so without this the same human received two full sequences, ten
    emails, from one campaign. The per-prospect guard cannot catch it because it
    only ever sees one prospect at a time.

    Ordering decides the winner, and prospects arrive ranked, so the best-matching
    company keeps the address. ``duplicates`` optionally collects what was skipped
    so the launch can report it rather than dropping prospects silently.
    """
    out, claimed = [], {}
    for prospect in result.get("prospects") or []:
        email = prospect.get("email") or {}
        to = (email.get("recipient") or {}).get("email") or email.get("to")
        if prospect.get("final_status") != "sendable":
            continue
        if (prospect.get("guard") or {}).get("decision") != "ALLOW":
            continue
        if email.get("status") != "ok" or not _valid_email(to):
            continue
        company = (prospect.get("company_name")
                   or prospect.get("research", {}).get("company_name") or "")
        key = str(to).strip().lower()
        if key in claimed:
            if duplicates is not None:
                duplicates.append({"to": to, "company": company,
                                   "already_launched_for": claimed[key]})
            continue
        claimed[key] = company
        out.append({
            "to": to,
            "company": company,
            "subject": email.get("subject") or "",
            "body": email.get("body") or "",
            "followups": prospect.get("followups") or [],
        })
    return out


def _cadence_warning(prospect: dict) -> dict | None:
    """A launchable prospect that would send only a 3-step cadence (no mid-cadence
    follow-ups) — surfaced so no one launches a silently incomplete sequence. The
    cause differs and is reported in the message: a MANUAL recipient never triggers
    follow-up generation (a code gap; follow-ups are only generated at preview for
    auto-'sendable' prospects), versus an auto-sendable prospect whose two slots
    were both dropped at create time (writer non-ok / guard BLOCK). Returns None for
    anything that is not launchable or that has follow-ups. Pure."""
    email = prospect.get("email") or {}
    to = (email.get("recipient") or {}).get("email") or email.get("to")
    launchable = (prospect.get("final_status") == "sendable"
                  and (prospect.get("guard") or {}).get("decision") == "ALLOW"
                  and email.get("status") == "ok" and _valid_email(to))
    if not launchable or (prospect.get("followups") or []):
        return None
    manual = bool(prospect.get("manual_recipient")) or bool((email.get("recipient") or {}).get("manual"))
    if manual:
        return {"code": "manual_recipient_no_followups", "planned_steps": 3,
                "message": ("Manual recipient, so no follow-ups were generated. This "
                            "prospect will send a 3-step cadence (initial + bump + "
                            "break-up), not the full 5-touch sequence.")}
    return {"code": "followups_dropped_at_create", "planned_steps": 3,
            "message": ("No follow-ups cleared generation at create time (the writer "
                        "or guard dropped both slots), so this prospect will send a "
                        "3-step cadence (initial + bump + break-up).")}


def _result_with_warnings(result: dict) -> dict:
    """Non-destructively enrich each prospect with a ``cadence_warning`` (or leave it
    absent). Computed on read so preview + detail always agree with launch."""
    if not isinstance(result, dict) or not isinstance(result.get("prospects"), list):
        return result
    enriched = []
    for p in result["prospects"]:
        pc = dict(p)
        warning = _cadence_warning(pc)
        if warning:
            pc["cadence_warning"] = warning
        # The canonical research trail, DERIVED from this prospect's real, persisted
        # stage outcomes (never a fabricated live animation). Attached only when a
        # stage genuinely produced a result, so a prospect that failed before any
        # stage ran carries no invented trail. Same shape as the Chat trail, so the
        # frontend renders it with the same <ResearchTrail> component.
        trail = campaign_trail.prospect_trail(pc)
        if trail:
            pc["research_trail"] = trail
        enriched.append(pc)
    out = dict(result)
    out["prospects"] = enriched
    return out


def _prospect_public_with_trail(pub: dict) -> dict:
    """A discovered prospect's public JSON, plus its canonical research trail when
    that trail is BACKED BY GENUINE EVIDENCE (a real discovery provider or a
    validated source link). A row that only has bare database fields — a name and
    nothing corroborating — gets no ``research_trail`` at all, so the UI never shows
    an evidence-less trail card for it."""
    trail = campaign_trail.discovery_trail(pub)
    has_evidence = any(e.get("provider") or e.get("sources") for e in trail)
    if trail and has_evidence:
        return {**pub, "research_trail": trail}
    return pub


# ── Launch-time cadence assembly (pure; no model or guard calls here) ────
_BUMP_INTRO = ("Floating this back to the top in case it got buried. No worries if "
               "the timing's off, just wanted to make sure it reached you.")


def _re_subject(subject: str) -> str:
    subject = (subject or "").strip()
    if subject[:3].lower() == "re:":
        return subject
    return f"Re: {subject}" if subject else "Re:"


def _quote_original(body: str) -> str:
    """The original email quoted reply-style, to sit under the bump line."""
    return "\n".join(f"> {ln}" if ln else ">" for ln in (body or "").strip().splitlines())


def _bump_step(item: dict) -> dict:
    """Touch 1 (day 3): the approved email verbatim, with a short human bump on top,
    threaded as a reply so it stays in the same conversation."""
    return {"subject": _re_subject(item["subject"]),
            "body": f"{_BUMP_INTRO}\n\n{_quote_original(item['body'])}",
            "to": item["to"], "delay_days": AUTOMATION_REPLY_WAIT_DAYS}


def _breakup_step(item: dict) -> dict:
    """Touch 4 (last): a short, low-pressure breakup. Generic on purpose — a
    template is the right tool, so no writer/guard call is made."""
    company = (item.get("company") or "").strip()
    who = f"the {company} team" if company else "you"
    body = (f"I'll leave it here so I'm not cluttering your inbox. If helping {who} "
            f"is ever worth a look, just reply to this and we'll pick it back up. "
            f"Either way, wishing you the best.")
    return {"subject": _re_subject(item["subject"]), "body": body,
            "to": item["to"], "delay_days": AUTOMATION_REPLY_WAIT_DAYS}


def _cadence_steps(item: dict) -> list:
    """The 5-touch step spec for one launchable prospect: approved email, verbatim
    bump, the surviving writer follow-ups (days 6/9), then the breakup. The engine
    schedules and stops them; the bump + breakup are always present, and the writer
    follow-ups fill the remaining follow-up budget."""
    steps = [{"subject": item["subject"], "body": item["body"], "to": item["to"],
              "delay_days": 0}]
    steps.append(_bump_step(item))
    room = max(0, AUTOMATION_MAX_FOLLOWUPS - 2)   # reserve slots for bump + breakup
    for fu in (item.get("followups") or [])[:room]:
        steps.append({"subject": fu["subject"], "body": fu["body"],
                      "to": item["to"], "delay_days": AUTOMATION_REPLY_WAIT_DAYS})
    steps.append(_breakup_step(item))
    return steps


def _valid_email(value: str) -> bool:
    """Well-formed AND capable of receiving mail.

    The domain check is deliberately applied to DISCOVERED addresses too, not
    just hand-typed ones: research/pipeline.py filters these at the source, but
    Apollo enrichment writes public_contact_email directly, so the send boundary
    must not assume every upstream did the same. Placeholder LOCAL parts stay a
    manual-entry concern only — a typed "founder@acme.com" is a guess, whereas a
    discovered one was actually published on the site and is often genuine.
    """
    if not (value and _EMAIL_RE.match(value)):
        return False
    domain = value.strip().lower().rpartition("@")[2]
    return not (domain in _PLACEHOLDER_DOMAINS or domain.endswith(".example"))


def _campaign_summary(row: dict) -> dict:
    summary = row["result"].get("summary") or {}
    return {
        "id": row["id"],
        "name": row["name"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "summary": summary,
        "workflow_ids": row["workflow_ids"],
    }


def _campaign_public(row: dict, idempotent: bool = False) -> dict:
    return {
        **_campaign_summary(row),
        "request": row["request"],
        "result": _result_with_warnings(row["result"]),
        "events": _safe_campaign_events(row["owner"], row["id"]),
        "idempotent": idempotent,
        "workflows": _safe_workflow_statuses(row["owner"], row["workflow_ids"]),
    }


def _safe_campaign_events(owner: str, campaign_id: str) -> list[dict]:
    try:
        return _campaign_store().events_for(owner, campaign_id)
    except Exception:  # noqa: BLE001 - do not turn a saved campaign into a 500
        log.exception("campaign_events_load_failed campaign_id=%s", campaign_id)
        return []


def _safe_workflow_statuses(owner: str, ids: list[str]) -> list[dict]:
    try:
        return _workflow_statuses(owner, ids)
    except Exception:  # noqa: BLE001 - campaign preview is still useful without workflow hydration
        log.exception("campaign_workflow_status_load_failed workflow_count=%s", len(ids or []))
        return []


def _workflow_statuses(owner: str, ids: list[str]) -> list[dict]:
    out = []
    for wid in ids or []:
        wf = _workflow_store().load(wid, user_id=owner)
        if wf is not None:
            out.append(engine.status(wf))
    return out


def _control_campaign(user: str, campaign_id: str, action: str):
    campaigns = _campaign_store()
    campaign = campaigns.get(user, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    if not campaign["workflow_ids"]:
        if action == "cancel":
            updated = campaigns.update(user, campaign_id, status="cancelled")
            campaigns.add_event(user, campaign_id, "cancelled", "Campaign preview cancelled before launch")
            return _campaign_public(updated)
        raise HTTPException(status_code=400, detail="Campaign has not been launched yet.")
    changed = 0
    for wid in campaign["workflow_ids"]:
        wf = getattr(engine, action)(_workflow_store(), user, wid)
        changed += int(wf is not None)
    status = {"pause": "paused", "resume": "launched", "cancel": "cancelled"}[action]
    updated = campaigns.update(user, campaign_id, status=status)
    campaigns.add_event(user, campaign_id, action, f"{changed} workflow(s) updated")
    return _campaign_public(updated)


def _campaign_store() -> CampaignStore:
    global _campaigns
    if _campaigns is None:
        _campaigns = CampaignStore()
    return _campaigns


def _prospect_store() -> ProspectStore:
    global _prospects
    if _prospects is None:
        _prospects = ProspectStore()
    return _prospects


def _workflow_store() -> WorkflowStore:
    global _workflows
    if _workflows is None:
        _workflows = WorkflowStore()
    return _workflows
