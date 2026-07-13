"""Email-writing agent: research data -> a short, human cold email.

Pipeline (mirrors the research engine's separation of concerns):

    research.py -> research_data -> writer.py -> writer_prompt (build) ->
    claude_client (ONE model call) -> writer_validator (validate + repair)

writer.py only orchestrates. Prompt text lives in ``writer_prompt``; output
validation/repair lives in ``writer_validator``; the actual API call goes
through ``services.claude_client`` (reused, unchanged) so the client stays
responsible only for model communication. This keeps prompt iteration, model
swaps, and A/B testing independent of the application logic.

Public API
----------
``write_email(research_data, *, add_reveal=False, max_repairs=...) -> dict``

Never raises for normal failures — always returns a structured result:

    {"status": "ok",   "subject": str, "body": str, "company": ..., "to": ...,
     "used_reveal": bool}
    {"status": "skip", "reason": str, ...}      # not enough detail / research skip
    {"status": "error","reason": str, ...}      # API failure / unfixable draft

The happy path is EXACTLY ONE Claude call. A draft that fails validation in a
way deterministic repair can't fix (banned wording or wrong length) triggers at
most ``max_repairs`` bounded regenerations (default 1); set 0 for strict
single-call behaviour.
"""

from agents import ai_voice
from agents import writer_prompt as prompt
from agents import writer_review as reviewer
from agents import writer_validator as validator
from agents.writer_prompt import first_name
from config.settings import (
    WRITER_MAX_REPAIRS,
    WRITER_MAX_TOKENS,
    WRITER_SELF_CRITIQUE,
    WRITER_SELF_CRITIQUE_ALWAYS,
)
from services import claude_client

# Field names that mark a dict as a research *data* payload (vs. the full result
# envelope research_company() returns, which carries a top-level "status"/"data").
_DATA_MARKERS = ("company_name", "has_enough_detail", "unique_hook", "founder_name")
_BLOCKED_QUALIFICATIONS = {"reject", "research_more"}
_NON_OUTREACH_STRATEGIES = {"hold", "research", "enrich"}


def write_email(research_data, *, add_reveal: bool = False,
                max_repairs: int = WRITER_MAX_REPAIRS,
                guidance: str = None, current_email: dict = None,
                allow_thin: bool = False, style_note: str = None) -> dict:
    """Turn research output into a ready-to-send cold email (or a skip/error).

    guidance / current_email support conversational REVISIONS driven by the chat
    layer ("make it shorter", "target the CTO", "drop that hook"): the model is
    shown the current draft and the requested change and revises it. When both
    are None (the default) this is the original single-shot draft behaviour.

    allow_thin: when the user explicitly asks for an email and only limited
    context exists (a summary, a couple of hooks, no full research), write a
    strong email from what IS available instead of skipping — still grounded,
    never fabricated. Off by default so the automated path keeps its "skip
    rather than send generic" discipline.

    Before returning, the draft is silently self-reviewed (writer_review); a weak
    one is improved within the SAME repair budget, so the happy path stays one
    Claude call. The score is internal and never surfaced.
    """
    data = _resolve_data(research_data)
    reason = _skip_reason(research_data, data, allow_thin=allow_thin)
    if reason:
        return _skip(reason, data)
    confidence = _confidence(research_data, data)

    try:
        draft = _generate(data, add_reveal, guidance=guidance,
                          current_email=current_email, allow_thin=allow_thin,
                          confidence=confidence, style_note=style_note)
        draft = validator.repair(draft, data, add_reveal)
        problems = validator.validate(draft, data, add_reveal)
        review = reviewer.review(draft, data)

        attempts = 0
        # Regenerate while HARD problems remain, or the draft is soft-weak and we
        # still have budget. Hard problems always take priority in the feedback.
        while (problems or review.weak) and attempts < max(0, max_repairs):
            feedback = list(problems) + (review.issues if review.weak else [])
            draft = _generate(data, add_reveal, feedback=feedback,
                             guidance=guidance, current_email=current_email,
                             allow_thin=allow_thin, confidence=confidence,
                             style_note=style_note)
            draft = validator.repair(draft, data, add_reveal)
            problems = validator.validate(draft, data, add_reveal)
            review = reviewer.review(draft, data)
            attempts += 1

        # HARD rules block a send; a lingering SOFT weakness does not — a grounded
        # draft is still returned (we improved it as far as the budget allowed).
        if problems:
            return _error(
                "Could not produce an email that meets quality rules; not "
                "sending a substandard draft.",
                data, problems=problems, draft=draft,
            )

        # Self-critique refine pass (a distinct EDITOR call). Runs only when the
        # free deterministic scan still smells AI in the draft (or _ALWAYS), so a
        # clean+specific email keeps the one-call happy path. Never runs on a
        # user-driven revision (we don't second-guess an explicit edit).
        if guidance is None:
            draft = _maybe_refine(draft, data, review, add_reveal,
                                  style_note=style_note)
            review = reviewer.review(draft, data)
        return _ok(draft, data, add_reveal, review=review)
    except claude_client.ClaudeClientError as exc:
        # Already a user-safe message (no secrets, no stack trace).
        return _error(str(exc), data)
    except Exception:  # noqa: BLE001 - last-resort guard; never crash a caller
        return _error("Unexpected error while writing the email.", data)


def write_subject_lines(research_data, *, current_email: dict = None,
                        count: int = 5) -> dict:
    """Generate five subject lines across distinct styles (curiosity/direct/
    conversational/minimalist/data-driven) in ONE call. Grounded, no clickbait.

    Returns {"status": "ok", "subjects": [{"style", "text"}], "company": ...} or
    a skip/error. Never raises for normal failures.
    """
    data = _resolve_data(research_data)
    if not isinstance(data, dict):
        return {"status": "skip", "reason": "No research to base subjects on.",
                "subjects": [], "company": None}
    try:
        raw = claude_client._call_model(
            prompt.SUBJECTS_SYSTEM_PROMPT, prompt.SUBJECTS_SCHEMA,
            prompt.build_subjects_content(data, current_email=current_email),
            max_tokens=WRITER_MAX_TOKENS,
            stage="writer",
        )
        subjects = _clean_subjects(raw, data, count)
        if not subjects:
            return _error("Could not generate usable subject lines.", data)
        return {"status": "ok", "subjects": subjects, "company": _company(data)}
    except claude_client.ClaudeClientError as exc:
        return _error(str(exc), data)
    except Exception:  # noqa: BLE001
        return _error("Unexpected error while generating subject lines.", data)


def write_variations(research_data, *, count: int = 3, add_reveal: bool = False,
                     allow_thin: bool = False, guidance: str = None,
                     style_note: str = None) -> dict:
    """Generate `count` (2-3) genuinely-different full emails in ONE call.

    Each variation is repaired + validated; only the ones that pass the hard
    rules are returned (so a bad variation never ships). `guidance` steers the
    whole set (e.g. the user disliked one and wants a fresh angle). Returns
    {"status": "ok", "variations": [{"label","angle","subject","body"}], ...}.
    """
    data = _resolve_data(research_data)
    reason = _skip_reason(research_data, data, allow_thin=allow_thin)
    if reason:
        return _skip(reason, data)
    try:
        raw = claude_client._call_model(
            prompt.SYSTEM_PROMPT, prompt.VARIATIONS_SCHEMA,
            prompt.build_variations_content(data, count=count, guidance=guidance,
                                            style_note=style_note),
            max_tokens=WRITER_MAX_TOKENS * 3,
            stage="writer",
        )
        variations = _clean_variations(raw, data, add_reveal, count)
        if not variations:
            return _error("Could not produce distinct, valid variations.", data)
        return {"status": "ok", "variations": variations,
                "company": _company(data), "to": _to(data)}
    except claude_client.ClaudeClientError as exc:
        return _error(str(exc), data)
    except Exception:  # noqa: BLE001
        return _error("Unexpected error while generating variations.", data)


def write_sequence(research_data, *, count: int = 4,
                   allow_thin: bool = False, style_note: str = None) -> dict:
    """Write a `count`-email outbound sequence in ONE call. Each email is a
    different step (angle/psychology/CTA) with a suggested send delay. Each is
    repaired + hard-validated; invalid steps are dropped. Returns
    {"status":"ok","emails":[{"step","angle","delay_days","subject","body"}], ...}.
    """
    data = _resolve_data(research_data)
    reason = _skip_reason(research_data, data, allow_thin=allow_thin)
    if reason:
        return _skip(reason, data)
    try:
        raw = claude_client._call_model(
            prompt.SYSTEM_PROMPT, prompt.SEQUENCE_SCHEMA,
            prompt.build_sequence_content(data, count=count, style_note=style_note),
            max_tokens=WRITER_MAX_TOKENS * 4,
            stage="writer",
        )
        emails = _clean_sequence(raw, data, count)
        if not emails:
            return _error("Could not produce a valid sequence.", data)
        return {"status": "ok", "emails": emails,
                "company": _company(data), "to": _to(data)}
    except claude_client.ClaudeClientError as exc:
        return _error(str(exc), data)
    except Exception:  # noqa: BLE001
        return _error("Unexpected error while writing the sequence.", data)


def write_followup(research_data, previous_email: dict, *,
                   max_repairs: int = WRITER_MAX_REPAIRS) -> dict:
    """Write a genuine follow-up that builds on a prior email (not a new first
    email). Requires the previous email; reuses research on file. One call
    (plus a bounded repair if it breaks hard rules)."""
    data = _resolve_data(research_data)
    if not isinstance(data, dict):
        return {"status": "skip", "reason": "No research to follow up from.",
                "subject": None, "body": None, "company": None}
    if not (isinstance(previous_email, dict) and previous_email.get("body")):
        return {"status": "skip",
                "reason": "There's no earlier email to follow up on yet.",
                "subject": None, "body": None, "company": _company(data)}
    try:
        draft = _gen_followup(data, previous_email)
        draft = validator.repair(draft, data, False)
        problems = validator.validate(draft, data, False)
        attempts = 0
        while problems and attempts < max(0, max_repairs):
            draft = _gen_followup(data, previous_email)
            draft = validator.repair(draft, data, False)
            problems = validator.validate(draft, data, False)
            attempts += 1
        if problems:
            return _error("Could not produce a clean follow-up.", data,
                          problems=problems, draft=draft)
        return _ok(draft, data, False, review=reviewer.review(draft, data))
    except claude_client.ClaudeClientError as exc:
        return _error(str(exc), data)
    except Exception:  # noqa: BLE001
        return _error("Unexpected error while writing the follow-up.", data)


def critique_email(email_text: str) -> dict:
    """Score a pasted email (hook/personalization/CTA/clarity/founder-voice/
    specificity/reply-likelihood 0-10) and suggest concrete improvements.

    Needs no research — evaluates the text itself. Returns
    {"status":"ok","scores":{...},"assessment":str,"suggestions":[...]}.
    """
    text = (email_text or "").strip()
    if len(text) < 15:
        return {"status": "skip",
                "reason": "Paste the email you'd like me to look at and I'll critique it."}
    try:
        raw = claude_client._call_model(
            prompt.CRITIQUE_SYSTEM_PROMPT, prompt.CRITIQUE_SCHEMA,
            prompt.build_critique_content(text), max_tokens=WRITER_MAX_TOKENS,
            stage="writer_critique",
        )
        if not isinstance(raw, dict) or "scores" not in raw:
            return {"status": "error", "reason": "Could not critique the email."}
        scores = {k: _clamp10(v) for k, v in (raw.get("scores") or {}).items()}
        return {"status": "ok", "scores": scores,
                "assessment": str(raw.get("assessment") or "").strip(),
                "suggestions": [str(s).strip() for s in (raw.get("suggestions") or [])
                                if str(s).strip()][:6]}
    except claude_client.ClaudeClientError as exc:
        return {"status": "error", "reason": str(exc)}
    except Exception:  # noqa: BLE001
        return {"status": "error", "reason": "Unexpected error while critiquing."}


def _gen_followup(data: dict, previous_email: dict) -> dict:
    raw = claude_client._call_model(
        prompt.FOLLOWUP_SYSTEM_PROMPT, prompt.WRITER_SCHEMA,
        prompt.build_followup_content(data, previous_email),
        max_tokens=WRITER_MAX_TOKENS,
        stage="writer",
    )
    if not isinstance(raw, dict):
        raise claude_client.ClaudeClientError("Unexpected response shape.")
    return {"subject": str(raw.get("subject") or "").strip(),
            "body": str(raw.get("body") or "").strip()}


def _clamp10(v):
    try:
        return max(0, min(10, int(round(float(v)))))
    except (TypeError, ValueError):
        return 0


# ──────────────────────────────────────────────────────────────────────
def _confidence(research_data, data):
    """A 0-100 confidence for HOW SPECIFIC to be. Prefer the research score when
    present; else infer from how many grounded fields we actually have."""
    if isinstance(research_data, dict):
        score = research_data.get("research_score")
        if isinstance(score, (int, float)):
            return int(score)
    if not isinstance(data, dict):
        return None
    signals = ("unique_hook", "recent_focus", "metrics_or_traction",
               "notable_customers", "their_mission_or_why", "product_category",
               "competitive_positioning", "additional_hooks")
    present = sum(1 for k in signals if data.get(k))
    return min(100, 20 + present * 12)     # rough, monotonic in grounded richness


def _generate(data: dict, add_reveal: bool, feedback=None,
              guidance=None, current_email=None, allow_thin=False,
              confidence=None, style_note=None) -> dict:
    """One structured-output Claude call -> {"subject", "body"} (strings)."""
    raw = claude_client._call_model(
        prompt.SYSTEM_PROMPT,
        prompt.WRITER_SCHEMA,
        prompt.build_user_content(data, feedback=feedback, guidance=guidance,
                                  current_email=current_email,
                                  allow_thin=allow_thin, confidence=confidence,
                                  style_note=style_note),
        max_tokens=WRITER_MAX_TOKENS,
        stage="writer",
    )
    if not isinstance(raw, dict):
        raise claude_client.ClaudeClientError(
            "The model returned an unexpected response shape."
        )
    return {
        "subject": str(raw.get("subject") or "").strip(),
        "body": str(raw.get("body") or "").strip(),
    }


# A draft this machine-sounding (0-100 proxy) earns the editor pass even with no
# single named tell — accumulated stiffness/rhythm is enough.
_AI_SCORE_REFINE_THRESHOLD = 34


def _maybe_refine(draft: dict, data: dict, review, add_reveal: bool,
                  *, style_note=None) -> dict:
    """Run the editor pass IFF the draft still reads AI, then adopt the result
    only if it's valid and genuinely less machine-sounding. Otherwise keep the
    original. Never raises; a refine failure silently returns the original draft."""
    if not WRITER_SELF_CRITIQUE:
        return draft
    reasons = _refine_reasons(draft, data, review)
    if not (WRITER_SELF_CRITIQUE_ALWAYS or _should_refine(draft, review, reasons)):
        return draft                       # clean + specific -> no second call

    core = {"subject": draft.get("subject", ""),
            "body": validator._strip_ps(draft.get("body", ""))}
    refined = _refine(core, data, reasons, style_note=style_note)
    if not refined:
        return draft
    refined = validator.repair(refined, data, add_reveal)
    if validator.validate(refined, data, add_reveal):
        return draft                       # editor produced an invalid email
    # Adopt only if it did not get MORE machine-sounding (strictly not worse).
    before = ai_voice.ai_score(validator._strip_ps(draft.get("body", "")))
    after = ai_voice.ai_score(validator._strip_ps(refined.get("body", "")))
    return refined if after <= before else draft


def _should_refine(draft: dict, review, reasons) -> bool:
    """Whether a HARD-valid draft is machine-sounding enough to earn the editor
    pass. Deliberately conservative so a clean, human email keeps the one-call
    happy path: a lone soft personalization note is NOT enough on its own."""
    body = (draft or {}).get("body") or ""
    if ai_voice.tells(body):                       # a structural tell survived
        return True
    if ai_voice.ai_score(body) >= _AI_SCORE_REFINE_THRESHOLD:
        return True
    # The regeneration loop already tried and the draft is STILL weak AND generic:
    # escalate to the editor (a different prompt) to break the swappable-copy rut.
    return bool(getattr(review, "weak", False)) and any(
        ("generic" in r.lower() or "grounded detail" in r.lower()
         or "prospect problem" in r.lower()) for r in reasons)


def _refine_reasons(draft: dict, data: dict, review) -> list:
    """Concrete, human-readable reasons the draft still reads AI (may be empty).

    Union of the deterministic structural tells and any 'generic/could-be-any-
    company' signals the self-review already surfaced. Passed to the editor so it
    fixes specific sentences instead of rewriting blindly."""
    body = (draft or {}).get("body") or ""
    reasons = list(ai_voice.tells(body))
    reasons += ai_voice.banned_hits(body)          # belt-and-suspenders (should be 0)
    for issue in (getattr(review, "issues", None) or []):
        low = issue.lower()
        if ("generic" in low or "grounded detail" in low or "marketing" in low
                or "prospect problem" in low):
            reasons.append(issue)
    # De-dupe, preserve order.
    return list(dict.fromkeys(r for r in reasons if str(r).strip()))


def _refine(draft: dict, data: dict, reasons, *, style_note=None):
    """One editor-persona model call: rewrite only the AI-sounding sentences.
    Returns {"subject","body"} or None. Never raises (a failure must not turn an
    already-acceptable draft into an error)."""
    try:
        raw = claude_client._call_model(
            prompt.REFINE_SYSTEM_PROMPT,
            prompt.WRITER_SCHEMA,
            prompt.build_refine_content(draft, data, tells=reasons),
            max_tokens=WRITER_MAX_TOKENS,
            stage="writer_refine",
        )
    except Exception:  # noqa: BLE001 - refine is best-effort; keep the original draft
        return None
    if not isinstance(raw, dict):
        return None
    subject = str(raw.get("subject") or "").strip()
    body = str(raw.get("body") or "").strip()
    if not subject or not body:
        return None
    return {"subject": subject, "body": body}


def _resolve_data(research_data):
    """Accept either the full research_company() result OR a bare data dict.

    Returns the inner data dict, or None when there's nothing usable.
    """
    if not isinstance(research_data, dict):
        return None
    if "data" in research_data or "status" in research_data:
        data = research_data.get("data")
        return data if isinstance(data, dict) else None
    if any(marker in research_data for marker in _DATA_MARKERS):
        return research_data  # a bare data payload was passed directly
    return None


def _skip_reason(research_data, data, allow_thin: bool = False):
    """Why we must NOT write (None == go ahead). We never send generic email.

    allow_thin relaxes the "enough detail / has a specific hook" gates for an
    EXPLICIT user request, so the writer can still produce a grounded email from
    a summary + a hook or two. It never relaxes grounding: a wholly-empty data
    payload, or an upstream research error/skip with no data, still skips.
    """
    status = research_data.get("status") if isinstance(research_data, dict) else None
    gate = _pipeline_gate_reason(research_data)
    if gate:
        return gate
    if status in ("skip", "error") and not (allow_thin and isinstance(data, dict)
                                            and _has_any_context(data)):
        return (
            (isinstance(research_data, dict)
             and (research_data.get("reason") or research_data.get("error")))
            or "Research did not yield a usable lead."
        )
    if not isinstance(data, dict):
        return "No research data to write from."
    if allow_thin:
        # Explicit request: proceed as long as there's SOMETHING to ground on.
        return None if _has_any_context(data) else (
            "There's nothing on file to write from yet — give me a company name "
            "or a detail to work with.")
    if not data.get("has_enough_detail"):
        return ("Not enough specific, verified detail to personalize — skipping "
                "(we never send generic emails).")
    if not _has_specifics(data):
        return "No specific hook to open with — skipping rather than guessing."
    return None


def _pipeline_gate_reason(research_data) -> str:
    """Block writing when callers pass full pipeline state showing this lead
    should not progress. Bare research-data calls keep their legacy behavior."""
    if not isinstance(research_data, dict):
        return None
    qualification = research_data.get("qualification")
    strategy = research_data.get("strategy")

    qrec = _lower_field(qualification, "recommendation")
    if qrec in _BLOCKED_QUALIFICATIONS:
        return f"Qualification is '{qrec}', so outreach should not be written."
    qconf = _numeric_field(qualification, "confidence")
    if qconf is not None and qconf < 40:
        return "Qualification confidence is too low to write outreach safely."

    if qualification is not None and strategy is None:
        return "No outreach strategy exists for this qualified lead."

    action = _lower_field(strategy, "recommended_action")
    if action in _NON_OUTREACH_STRATEGIES:
        return f"Strategy action is '{action}', so no outbound email should be written."
    if strategy is not None and not action:
        return "Strategy is missing a send-ready action."
    return None


def _lower_field(obj, field: str) -> str:
    if isinstance(obj, dict):
        value = obj.get(field)
    else:
        value = getattr(obj, field, None)
    return str(value or "").strip().lower()


def _numeric_field(obj, field: str):
    if isinstance(obj, dict):
        value = obj.get(field)
    else:
        value = getattr(obj, field, None)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _has_any_context(data: dict) -> bool:
    """True if there is ANY grounded material to write from (thin mode)."""
    if not isinstance(data, dict):
        return False
    if _has_specifics(data):
        return True
    return any(_nonempty(data.get(k)) for k in
               ("company_name", "what_they_do", "target_customer",
                "product_category", "competitive_positioning"))


def _has_specifics(data: dict) -> bool:
    """At least one concrete, openable detail must exist."""
    if _nonempty(data.get("unique_hook")):
        return True
    if _nonempty_list(data.get("additional_hooks")):
        return True
    if _nonempty_list(data.get("notable_customers")):
        return True
    return any(_nonempty(data.get(key)) for key in
               ("metrics_or_traction", "recent_focus", "their_mission_or_why"))


# ── Post-processing for subjects / variations ──────────────────────────
def _clean_subjects(raw, data, count) -> list:
    """Validate + de-dupe model subjects; drop any with banned wording."""
    if not isinstance(raw, dict):
        return []
    allowed = validator._allowed_terms(data)
    out, seen = [], set()
    for item in raw.get("subjects") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip().strip('"').strip("'")
        text = text[:90].rstrip()
        key = text.lower()
        if not text or key in seen:
            continue
        if validator.find_banned(text, allowed):
            continue
        seen.add(key)
        try:
            rating = max(1, min(5, int(item.get("rating"))))
        except (TypeError, ValueError):
            rating = 3
        out.append({"style": item.get("style") or "direct", "text": text,
                    "rating": rating,
                    "reason": str(item.get("reason") or "").strip()[:60]})
    # Rank strongest first so the best subject leads.
    out.sort(key=lambda s: s["rating"], reverse=True)
    return out[:max(1, count)]


def _clean_variations(raw, data, add_reveal, count) -> list:
    """Repair + hard-validate each variation; keep only distinct, valid ones."""
    if not isinstance(raw, dict):
        return []
    labels = ["A", "B", "C", "D"]
    out, seen_bodies = [], set()
    for i, item in enumerate(raw.get("variations") or []):
        if not isinstance(item, dict):
            continue
        draft = {"subject": str(item.get("subject") or "").strip(),
                 "body": str(item.get("body") or "").strip()}
        draft = validator.repair(draft, data, add_reveal)
        if validator.validate(draft, data, add_reveal):
            continue                       # drop a variation that breaks hard rules
        sig = draft["body"][:60].lower()
        if not draft["body"] or sig in seen_bodies:
            continue                       # drop an empty or near-duplicate body
        seen_bodies.add(sig)
        out.append({"label": labels[len(out)], "angle": str(item.get("angle") or "").strip(),
                    "subject": draft["subject"], "body": draft["body"]})
        if len(out) >= max(2, min(int(count or 3), 3)):
            break
    return out


def _clean_sequence(raw, data, count) -> list:
    """Repair + hard-validate each sequence email; keep valid ones in send order."""
    if not isinstance(raw, dict):
        return []
    out, seen = [], set()
    for item in raw.get("emails") or []:
        if not isinstance(item, dict):
            continue
        draft = {"subject": str(item.get("subject") or "").strip(),
                 "body": str(item.get("body") or "").strip()}
        draft = validator.repair(draft, data, False)
        if validator.validate(draft, data, False):
            continue
        sig = draft["body"][:50].lower()
        if not draft["body"] or sig in seen:
            continue
        seen.add(sig)
        try:
            delay = max(0, int(item.get("delay_days")))
        except (TypeError, ValueError):
            delay = [0, 3, 4, 5, 6, 7][min(len(out), 5)]
        out.append({"step": len(out) + 1, "angle": str(item.get("angle") or "").strip(),
                    "delay_days": delay, "subject": draft["subject"],
                    "body": draft["body"]})
        if len(out) >= max(2, min(int(count or 4), 6)):
            break
    return out


# ── Result builders ────────────────────────────────────────────────────
def _ok(draft: dict, data: dict, add_reveal: bool, review=None) -> dict:
    result = {
        "status": "ok",
        "subject": draft["subject"],
        "body": draft["body"],
        "company": _company(data),
        "to": _to(data),
        "used_reveal": bool(add_reveal),
    }
    # Internal quality score kept for reference; a display-ready breakdown
    # (Overall/Hook/Personalization/Founder Voice/CTA/Reply Likelihood/Spam Risk/
    # Reading Time) is computed deterministically here — no extra model call.
    if review is not None:
        result["review"] = {"score": review.score, "dimensions": review.dimensions}
    result["quality"] = reviewer.quality_report(draft, data)
    return result


def _skip(reason: str, data) -> dict:
    return {"status": "skip", "reason": reason, "subject": None, "body": None,
            "company": _company(data), "to": _to(data), "used_reveal": False}


def _error(reason: str, data, problems=None, draft=None) -> dict:
    result = {"status": "error", "reason": reason, "subject": None, "body": None,
              "company": _company(data), "to": _to(data), "used_reveal": False}
    if problems:
        result["problems"] = problems
    if draft:                       # kept for logging/debugging — NOT for sending
        result["rejected_draft"] = draft
    return result


# ── Tiny helpers ───────────────────────────────────────────────────────
def _company(data):
    return data.get("company_name") if isinstance(data, dict) else None


def _to(data):
    if not isinstance(data, dict):
        return None
    name = data.get("primary_contact_name") or data.get("founder_name")
    if name:
        return first_name(name)
    return (data.get("public_contact_email") or data.get("recipient_route")
            or data.get("linkedin_url") or data.get("contact_page_url"))


def _nonempty(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _nonempty_list(value) -> bool:
    return isinstance(value, list) and any(str(item).strip() for item in value)
