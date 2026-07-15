"""Campaign orchestration HTTP API.

This is a thin, durable orchestration layer over the existing agents. It does
not implement research/writing logic itself; it calls Discovery -> Research ->
Qualification -> Strategy -> Writer -> Guard, persists a preview, and only
launches automation workflows after an explicit user action.
"""

import hashlib
import json
import logging
import time
from typing import Literal

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from agents import qualification, strategy, writer
from automation import engine
from automation.store import WorkflowStore
from config.settings import AUTOMATION_MAX_FOLLOWUPS, AUTOMATION_REPLY_WAIT_DAYS
from discovery.engine import discover
from discovery.models import DiscoveryQuery
from discovery.store import ProspectStore
from guard import assess as guard_assess
from guard.models import BLOCK
from research.pipeline import research_company
from server.auth import require_user
from server.campaign_store import CampaignStore

_campaigns = None
_prospects = None
_workflows = None
log = logging.getLogger("saqua.campaign_api")

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

    @app.post("/api/campaigns")
    def create_campaign(body: CampaignCreate, request: Request,
                        _=Depends(_write), user: str = Depends(require_user)):
        key = body.idempotency_key or request.headers.get("Idempotency-Key") or _request_hash(user, body)
        trace_id = (request.headers.get("x-request-id") or request.headers.get("x-saqua-proxy-trace-id") or key[:12]).strip()
        user_hash = hashlib.sha256(user.encode("utf-8")).hexdigest()[:12]
        log.info(
            "campaign_create_start trace_id=%s user_hash=%s name=%r limit=%s raw_len=%s",
            trace_id, user_hash, body.name, body.limit, len(body.icp.raw or ""),
        )
        try:
            campaigns = _campaign_store()
            existing = campaigns.get_by_idempotency_key(user, key)
            if existing:
                log.info("campaign_create_idempotent trace_id=%s campaign_id=%s", trace_id, existing["id"])
                return _campaign_public(existing, idempotent=True)

            result, status, events = _run_pipeline(user, body, trace_id=trace_id)
            log.info("campaign_stage_start trace_id=%s stage=persist status=%s", trace_id, status)
            campaign = campaigns.create(
                owner=user, idempotency_key=key, name=body.name,
                request=body.model_dump(), result=result, status=status,
            )
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
                detail=(f"Campaign orchestration failed unexpectedly (reference {trace_id}). "
                        "This was logged server-side — please retry; if it persists the "
                        "campaign service or database may be unavailable."),
            ) from exc

    @app.get("/api/campaigns")
    def list_campaigns(request: Request, _=Depends(_read),
                       user: str = Depends(require_user)):
        rows = _campaign_store().list_for_owner(user)
        return {"campaigns": [_campaign_summary(row) for row in rows]}

    @app.get("/api/campaigns/{campaign_id}")
    def get_campaign(campaign_id: str, request: Request, _=Depends(_read),
                     user: str = Depends(require_user)):
        campaign = _campaign_store().get(user, campaign_id)
        if campaign is None:
            raise HTTPException(status_code=404, detail="Campaign not found.")
        return _campaign_public(campaign)

    @app.get("/api/prospects")
    def list_prospects(request: Request, _=Depends(_read),
                       user: str = Depends(require_user)):
        return {"prospects": [p.public() for p in _prospect_store().list_for_owner(user)]}

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
        if campaign["status"] == "launched" and campaign["workflow_ids"]:
            return _campaign_public(campaign)

        launchable = _launchable_emails(campaign["result"])
        if not launchable:
            raise HTTPException(status_code=400, detail="No guard-approved emails with valid recipient addresses.")

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
        updated = campaigns.update(user, campaign_id, status="launched",
                                   result=result, workflow_ids=workflow_ids)
        campaigns.add_event(user, campaign_id, "launched",
                            f"{len(workflow_ids)} workflow(s) created via {body.provider}")
        return _campaign_public(updated)

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

    def event(type_: str, detail: str) -> None:
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
        reason = ("Prospect discovery could not complete — a data provider errored or timed out, "
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

    prospects = []
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

    for prospect in discovery.prospects:
        prospects.append(_process_prospect(user, prospect, body.icp.model_dump(), event, trace_id=trace_id))

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
        reason = ("No launchable prospects — every email was blocked, held, route-only, or lacked a "
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


def _launchable_emails(result: dict) -> list[dict]:
    out = []
    for prospect in result.get("prospects") or []:
        email = prospect.get("email") or {}
        to = (email.get("recipient") or {}).get("email") or email.get("to")
        if prospect.get("final_status") != "sendable":
            continue
        if (prospect.get("guard") or {}).get("decision") != "ALLOW":
            continue
        if email.get("status") != "ok" or not _valid_email(to):
            continue
        out.append({
            "to": to,
            "company": prospect.get("company_name") or prospect.get("research", {}).get("company_name") or "",
            "subject": email.get("subject") or "",
            "body": email.get("body") or "",
            "followups": prospect.get("followups") or [],
        })
    return out


# ── Launch-time cadence assembly (pure; no model or guard calls here) ────
_BUMP_INTRO = ("Floating this back to the top in case it got buried — no worries if "
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
    return bool(value and _EMAIL_RE.match(value))


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
        "result": row["result"],
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
