"""Tools: each product CAPABILITY as a callable, plug-in tool.

A ``Tool`` bundles an Anthropic tool schema with a handler that reads/writes the
conversation's shared workspace. The agent (chat/agent.py) never hardcodes what
to do — it calls these by name. New capabilities (send email, find prospects,
handle replies, LinkedIn) drop in by defining a Tool and adding it to REGISTRY;
nothing else changes. The stubs below are real registered tools so the agent can
already discover and gracefully defer them.

Handlers return a ``ToolResult``:
  * summary            — text fed back to the model (what happened)
  * message            — an optional rich Message appended to the visible thread
  * workspace_updates  — dict merged into conversation.workspace
Handlers never raise for normal failures; they report via ``summary``.
"""

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from urllib.parse import urlparse

from agents import qualification
from agents import strategy
from agents.research import research_company
from guard import assess as _guard_assess
from telemetry import record_event as _tele_event
from agents import writer_review as _writer_review
from chat import style as _style
from agents.writer import (
    critique_email,
    write_email,
    write_followup,
    write_sequence,
    write_subject_lines,
    write_variations,
)
from chat import resolver
from chat import research_pipeline
from chat.context import intel_digest, research_digest
from chat.models import CHANNEL, EMAIL, PROSPECTS, RESEARCH, Message
from agents import channels
from discovery import engine as discovery_engine
from discovery.models import DiscoveryQuery
from research import orchestrator


@dataclass
class ToolResult:
    summary: str
    message: Optional[Message] = None
    workspace_updates: dict = field(default_factory=dict)
    # Some tools append MORE than one card in a turn (e.g. A/B/C variations).
    messages: list = field(default_factory=list)


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict, object], ToolResult]

    def spec(self) -> dict:
        """Anthropic tool-use schema."""
        return {"name": self.name, "description": self.description,
                "input_schema": self.input_schema}


# ──────────────────────────────────────────────────────────────────────
#  URL / name resolution (best-effort: first message may be a name OR a site)
# ──────────────────────────────────────────────────────────────────────
def resolve_url(query: str):
    """Turn a company website OR a bare company name into a URL to research.

    A name is a best-effort guess (``Acme Robotics`` -> ``https://acmerobotics.com``);
    if that fails to resolve, the research tool reports it and the agent asks the
    user for the website. Returns (url, was_name_guess) or (None, False).
    """
    q = (query or "").strip()
    if not q:
        return None, False
    if "://" in q:
        return q, False
    if " " not in q and "." in q:          # looks like a bare domain
        return "https://" + q, False
    slug = re.sub(r"[^a-z0-9]", "", q.lower())
    if not slug:
        return None, False
    return f"https://{slug}.com", True     # a name -> guessed domain


def _company_label(result: dict, fallback_url: str) -> str:
    data = (result or {}).get("data") or {}
    name = data.get("company_name")
    if name:
        return name
    host = urlparse(fallback_url or "").hostname or fallback_url or "Company"
    return host[4:] if host.startswith("www.") else host


# ──────────────────────────────────────────────────────────────────────
#  Tool: resolve a company NAME to its official website (search-backed)
#  Separate from research: research still takes URLs exactly as before.
# ──────────────────────────────────────────────────────────────────────
def _tool_resolve_company(inp: dict, conversation) -> ToolResult:
    query = str(inp.get("query") or "").strip()
    if not query:
        return ToolResult(summary="No company name or URL was provided to resolve.")

    # 1) The user already gave a URL/domain -> skip the lookup entirely.
    url, guessed = resolve_url(query)
    if url and not guessed:
        return ToolResult(
            summary=f"'{query}' is already a website; research it directly with "
                    f"research_company using {url} (no lookup needed).",
            workspace_updates={"company_url": url,
                               "resolution": {"query": query, "url": url}})

    # 2) Cached in this thread -> only look up once.
    cached = conversation.workspace.get("resolution")
    if cached and cached.get("url") and (
            not query or resolver._norm(query) == resolver._norm(cached.get("query", ""))):
        return ToolResult(
            summary=f"'{query}' was already resolved to {cached['url']} (cached). "
                    "Use research_company with that URL.")

    # 3) Search for the official site.
    result = resolver.resolve_company_name(query)
    status = result.get("status")

    if status == "resolved":
        official = result["url"]
        return ToolResult(
            summary=f"Resolved '{query}' to its official website: {official}. Now "
                    "research it by calling research_company with this URL.",
            workspace_updates={"company_url": official,
                               "resolution": {"query": query, "url": official}})

    if status == "choices":
        options = result["choices"]
        listed = "\n".join(
            f"  {i}. {c['domain']} — {c.get('title') or c['url']}"
            for i, c in enumerate(options, start=1))
        return ToolResult(
            summary=("Several companies could match '" + query + "'. ASK THE USER "
                     "which one they mean (do not research yet):\n" + listed))

    if status == "no_provider":
        guess, _ = resolve_url(query)
        return ToolResult(
            summary=("No web-search API is configured (set TAVILY_API_KEY or "
                     "BRAVE_API_KEY to enable exact company lookup). Best-effort "
                     f"guess for '{query}' is {guess} — ask the user to confirm "
                     "the website, or research the guess if they'd rather."))

    if status == "error":
        return ToolResult(
            summary=f"The company lookup for '{query}' failed. Ask the user for "
                    "the company's website URL.")

    return ToolResult(  # "none"
        summary=f"Couldn't find an official website for '{query}'. Ask the user "
                "for the company's website URL.")


# ──────────────────────────────────────────────────────────────────────
#  Tool: research a company (reuses cached research when possible)
# ──────────────────────────────────────────────────────────────────────
def _tool_research(inp: dict, conversation) -> ToolResult:
    query = str(inp.get("query") or "").strip()
    refresh = bool(inp.get("refresh"))
    find_founder = bool(inp.get("find_founder"))

    existing = conversation.workspace.get("research")
    if existing and existing.get("status") == "ok" and not refresh and not find_founder:
        label = conversation.workspace.get("company") or "this company"
        return ToolResult(
            summary=(f"Research for {label} is already on file — reuse it, do not "
                     "research again. Current facts:\n" + research_digest(existing)))

    if not query and existing:
        query = conversation.workspace.get("company_url") or ""
    url, guessed = resolve_url(query)
    if not url:
        return ToolResult(summary="No company website or name was provided to research.")

    try:
        result = research_company(url, find_founder=find_founder)
    except Exception:  # noqa: BLE001 - research is designed not to raise, belt-and-braces
        return ToolResult(summary=f"Research failed unexpectedly for {url}.")

    status = result.get("status")
    if status == "error":
        hint = (" I guessed that URL from the name — ask the user for the exact "
                "company website." if guessed else "")
        return ToolResult(summary=f"Could not research {url}: {result.get('error')}.{hint}")

    label = _company_label(result, url)
    updates = {"research": result, "company": label, "company_url": url}

    if status == "skip":
        return ToolResult(
            summary=(f"Researched {label} but found too little to personalize: "
                     f"{result.get('reason')}. Tell the user honestly; do not invent."),
            workspace_updates=updates)

    card = Message(
        role="assistant", kind=RESEARCH,
        content=f"Researched **{label}**.",
        data={
            "company": label,
            "what_they_do": (result.get("data") or {}).get("what_they_do"),
            "research_score": result.get("research_score"),
            "pages_crawled": result.get("pages_crawled") or [],
            "stop_reason": result.get("stop_reason"),
            "hooks": [h.get("text") for h in (result.get("hooks") or [])[:5]],
            "digest": research_digest(result),
        })
    return ToolResult(
        summary=(f"Researched {label} (score {result.get('research_score')}/100, "
                 f"{len(result.get('pages_crawled') or [])} pages). Facts now on "
                 "file:\n" + research_digest(result)
                 + "\n\nNow draft the email with write_email."),
        message=card, workspace_updates=updates)


# ──────────────────────────────────────────────────────────────────────
#  Tool: deep, multi-source research (orchestrator: website + news + long-form)
# ──────────────────────────────────────────────────────────────────────
def _tool_deep_research(inp: dict, conversation) -> ToolResult:
    """Gather multi-source intelligence and synthesize a cited briefing.

    Talks ONLY to research.orchestrator (never a provider directly). The
    orchestrator picks the providers the request needs, runs them concurrently,
    caches, degrades gracefully, and Anthropic-synthesizes grounded findings +
    personalization hooks with citations.
    """
    query = str(inp.get("company") or "").strip()
    focus = str(inp.get("focus") or "").strip()

    # Resolve the target: explicit input, else the thread's known company/url.
    url = None
    company = None
    if query:
        maybe_url, guessed = resolve_url(query)
        if maybe_url and not guessed:
            url = maybe_url
        else:
            company = query
            url = conversation.workspace.get("company_url")
    else:
        company = conversation.workspace.get("company")
        url = conversation.workspace.get("company_url")
    if not company and not url:
        return ToolResult(summary="No company or website was given to research. "
                          "Ask the user which company they'd like me to look into.")

    try:
        intel = orchestrator.research(company, url=url, focus=focus)
    except Exception:  # noqa: BLE001 - orchestrator is designed not to raise
        return ToolResult(summary="The research providers couldn't be reached just now.")

    status = intel.get("status")
    if status == "error":
        return ToolResult(summary="Multi-source research hit a problem: "
                          + (intel.get("error") or "unknown error") + ".")
    if status == "empty":
        missing = ", ".join(intel.get("providers_missing") or []) or "none"
        return ToolResult(
            summary=(f"Researched {intel.get('company')} but the providers returned "
                     f"nothing usable (unconfigured providers: {missing}). Tell the "
                     "user honestly; do not invent details."),
            workspace_updates={"intel": intel})

    label = intel.get("company") or company or "the company"
    card = Message(
        role="assistant", kind=RESEARCH,
        content=f"Researched **{label}** across {len(intel.get('sources') or [])} sources.",
        data={
            "company": label,
            "what_they_do": intel.get("summary"),
            "research_score": None,
            "pages_crawled": [s.get("url") for s in (intel.get("sources") or [])],
            "stop_reason": "multi-source synthesis",
            "hooks": [h.get("text") for h in (intel.get("hooks") or [])[:5]],
            "digest": intel_digest(intel),
        })
    return ToolResult(
        summary=("Multi-source research complete for " + label + ". Reference these "
                 "findings and hooks naturally in your reply, and mention a source "
                 "where it helps; don't dump the whole list.\n\n" + intel_digest(intel)),
        message=card, workspace_updates={"intel": intel,
                                         "company": label,
                                         **({"company_url": url} if url else {})})


# ──────────────────────────────────────────────────────────────────────
#  Writer source: merge grounded website research with multi-source intel
# ──────────────────────────────────────────────────────────────────────
def _writer_source(workspace: dict):
    """Build the writer's input from what the thread already knows.

    Returns (research_arg, allow_thin) where research_arg is what write_email
    accepts. Grounded website research is the base; the multi-source intel from
    the orchestrator (deep_research) AUGMENTS it with hooks / a summary / recent
    signal — it never overrides a grounded fact. With no full research but intel
    on file, we write from the intel (thin mode). With only a company label, we
    still allow a plain, honest email. Never fabricates.
    """
    research = workspace.get("research")
    intel = workspace.get("intel")
    intel_ok = bool(intel and intel.get("status") == "ok")

    if research and research.get("status") == "ok":
        data = dict(research.get("data") or {})
        if intel_ok:
            _overlay_intel(data, intel)
        source = {"status": "ok", "data": data}
        if isinstance(workspace.get("qualification"), dict):
            source["qualification"] = workspace["qualification"]
        if isinstance(workspace.get("strategy"), dict):
            source["strategy"] = workspace["strategy"]
        return source, False

    if intel_ok:
        source = {"status": "ok", "data": _data_from_intel(intel)}
        if isinstance(workspace.get("qualification"), dict):
            source["qualification"] = workspace["qualification"]
        if isinstance(workspace.get("strategy"), dict):
            source["strategy"] = workspace["strategy"]
        return source, True

    company = workspace.get("company")
    if company:
        source = {"status": "ok", "data": {"company_name": company,
                                           "has_enough_detail": False}}
        if isinstance(workspace.get("qualification"), dict):
            source["qualification"] = workspace["qualification"]
        if isinstance(workspace.get("strategy"), dict):
            source["strategy"] = workspace["strategy"]
        return source, True
    return None, False


def _overlay_intel(data: dict, intel: dict) -> None:
    """Augment grounded research data with intel hooks/summary (never override)."""
    hook_texts = [h.get("text") for h in (intel.get("hooks") or []) if h.get("text")]
    finding_texts = [f.get("text") for f in (intel.get("findings") or [])
                     if f.get("text")]
    if not data.get("unique_hook") and hook_texts:
        data["unique_hook"] = hook_texts[0]
    extra = list(data.get("additional_hooks") or [])
    for t in hook_texts + finding_texts:
        if t and t not in extra:
            extra.append(t)
    data["additional_hooks"] = extra[:8]
    if not data.get("what_they_do") and intel.get("summary"):
        data["what_they_do"] = intel["summary"]
    if not data.get("recent_focus") and finding_texts:
        data["recent_focus"] = finding_texts[0]
    data["has_enough_detail"] = True


def _data_from_intel(intel: dict) -> dict:
    """A writer data payload built purely from multi-source intel (no website
    evidence pipeline result on file)."""
    hooks = [h.get("text") for h in (intel.get("hooks") or []) if h.get("text")]
    findings = [f.get("text") for f in (intel.get("findings") or []) if f.get("text")]
    return {
        "company_name": intel.get("company"),
        "what_they_do": intel.get("summary"),
        "unique_hook": hooks[0] if hooks else (findings[0] if findings else None),
        "additional_hooks": (hooks[1:] + findings)[:8],
        "recent_focus": findings[0] if findings else None,
        "has_enough_detail": bool(hooks or findings),
    }


# ──────────────────────────────────────────────────────────────────────
#  Tool: write / revise / subjects / variations (reuses research + intel)
# ──────────────────────────────────────────────────────────────────────
def _tool_write_email(inp: dict, conversation) -> ToolResult:
    mode = str(inp.get("mode") or "").strip().lower() or "auto"
    guidance = str(inp.get("guidance") or "").strip() or None
    count = inp.get("count")

    # Critique needs only the pasted email text — no company/research required.
    if mode == "critique":
        return _do_critique(inp, conversation)
    if mode == "compare":
        return _do_compare(conversation)

    source, allow_thin = _writer_source(conversation.workspace)
    if source is None:
        return ToolResult(
            summary="There's no company on file yet. Ask the user which company "
                    "this email is for (or research one first).")

    if mode == "subjects":
        return _do_subjects(source, conversation, count)
    if mode == "variations":
        return _do_variations(source, conversation, count, allow_thin, guidance)
    if mode == "follow_up":
        return _do_followup(source, conversation)
    if mode == "sequence":
        return _do_sequence(source, conversation, count, allow_thin)
    return _do_email(source, conversation, guidance, allow_thin)


def _style_note(conversation) -> str:
    return _style.profile_note(conversation.workspace.get("style_profile") or {})


# ── Structured writing artifacts (internal, future-proofing) ───────────
# Every writing capability yields the same card shape via _artifact_card, and
# each artifact carries a STABLE, slot-based id ("email", "version-b",
# "email-3", "followup"). These ids are internal only — the API response
# whitelist omits `id`, so they never reach the browser or the user. They exist
# so future automation can reference a specific artifact ("rewrite email 3",
# "edit version B") without re-architecting the writer.
def _artifact_card(art_id, content, *, subject, body, company, to,
                   label=None, angle=None) -> Message:
    data = {"id": art_id, "subject": subject, "body": body,
            "company": company, "to": to}
    if label is not None:
        data["label"] = label
    if angle is not None:
        data["angle"] = angle
    return Message(role="assistant", kind=EMAIL, content=content, data=data)


def _variation_id(label) -> str:
    return "version-" + str(label).strip().lower().replace(" ", "-")


def _do_sequence(source, conversation, count, allow_thin) -> ToolResult:
    try:
        result = write_sequence(source, count=int(count) if count else 4,
                                allow_thin=allow_thin,
                                style_note=_style_note(conversation))
    except Exception:  # noqa: BLE001
        return ToolResult(summary="The sequence couldn't be generated just now.")
    emails = result.get("emails") or []
    if result.get("status") != "ok" or not emails:
        return ToolResult(summary="Could not produce a sequence: "
                          + (result.get("reason") or "no valid steps") + ".")
    for e in emails:
        e["id"] = f"email-{e['step']}"           # stable slot id (internal)
    messages = [_artifact_card(
        e["id"], f"Email {e['step']} of the sequence.",
        subject=e["subject"], body=e["body"], company=result.get("company"),
        to=result.get("to"), label=f"Email {e['step']} · day {e['delay_days']}",
        angle=e.get("angle")) for e in emails]
    steps = "; ".join(f"Email {e['step']} (day {e['delay_days']}, {e.get('angle')})"
                      for e in emails)
    return ToolResult(
        summary=(f"Wrote a {len(emails)}-email sequence, each shown as its own card "
                 "with its send-day label. Briefly tell the user the shape of the "
                 "sequence (one short line naming each step's angle and timing): "
                 + steps + ". Don't repeat the full email text."),
        messages=messages,
        workspace_updates={"sequence": emails})


def _do_followup(source, conversation) -> ToolResult:
    prev = conversation.workspace.get("email")
    prev = prev if (prev and prev.get("status") == "ok") else None
    if not prev:
        return ToolResult(
            summary="There's no earlier email in this thread to follow up on. "
                    "Offer to draft the first email instead.")
    try:
        email = write_followup(source, prev)
    except Exception:  # noqa: BLE001
        return ToolResult(summary="The follow-up couldn't be written just now.")
    if email.get("status") != "ok":
        return ToolResult(summary="Could not write a follow-up: "
                          + (email.get("reason") or "unknown reason") + ".")
    email["id"] = "followup"
    card = _artifact_card(
        "followup", "Drafted a follow-up.", subject=email.get("subject"),
        body=email.get("body"), to=email.get("to"), company=email.get("company"),
        label="Follow-up")
    return ToolResult(
        summary="Drafted a follow-up that builds on the previous email (shown as a "
                "card). Give a one-line note; don't repeat the email text.",
        message=card, workspace_updates={"email": email})


def _do_critique(inp, conversation) -> ToolResult:
    # The email to critique: explicit text, else the current draft on file.
    text = str(inp.get("email_text") or "").strip()
    if not text:
        cur = conversation.workspace.get("email")
        if cur and cur.get("status") == "ok" and cur.get("body"):
            text = f"Subject: {cur.get('subject','')}\n\n{cur.get('body','')}"
    if not text:
        return ToolResult(summary="Ask the user to paste the email they'd like "
                          "critiqued (or draft one first).")
    try:
        result = critique_email(text)
    except Exception:  # noqa: BLE001
        return ToolResult(summary="The critique couldn't be produced just now.")
    if result.get("status") != "ok":
        return ToolResult(summary=result.get("reason") or "Could not critique that.")
    scores = result.get("scores") or {}
    score_line = ", ".join(f"{k.replace('_',' ')} {v}/10" for k, v in scores.items())
    sugg = "\n".join(f"  - {s}" for s in result.get("suggestions") or [])
    return ToolResult(
        summary=("Email critique (present this to the user clearly — the scores as "
                 "a short list, then the assessment, then the suggestions):\n"
                 f"Scores: {score_line}\nAssessment: {result.get('assessment','')}\n"
                 f"Suggestions:\n{sugg}"),
        workspace_updates={"last_critique": result})


def _do_email(source, conversation, guidance, allow_thin) -> ToolResult:
    current = conversation.workspace.get("email") if guidance else None
    current = current if (current and current.get("status") == "ok") else None
    prev_body = (current or {}).get("body") if current else None

    # Write with the user's learned standing preferences already applied (the
    # agent learns them from each message; here we just honor them). Also fold in
    # this specific guidance so an in-the-moment "no emojis" applies right away.
    profile = conversation.workspace.get("style_profile") or _style.default_profile()
    if guidance:
        profile = _style.learn_from_guidance(profile, guidance)
    style_note = _style.profile_note(profile)
    try:
        email = write_email(source, guidance=guidance, current_email=current,
                            allow_thin=allow_thin, style_note=style_note)
    except Exception:  # noqa: BLE001
        return ToolResult(summary="The email could not be written just now.")

    if email.get("status") != "ok":
        return ToolResult(
            summary=("Could not write the email: "
                     + (email.get("reason") or "unknown reason") + "."),
            workspace_updates={"email": email})

    verb = "Revised" if guidance else "Drafted"
    detail = f" ({guidance})" if guidance else ""
    email["id"] = "email"                        # stable slot id for the live draft
    card = _artifact_card(
        "email", f"{verb} the email{detail}.", subject=email.get("subject"),
        body=email.get("body"), to=email.get("to"), company=email.get("company"))

    # Deterministic extras (no model call): a compact quality read the assistant
    # can share, and — on a revision — a one-line explanation of what changed.
    note = _quality_note(email.get("quality"))
    explain = _writer_review.explain_change(prev_body, email.get("body")) if prev_body else ""
    guidance_hint = (" Then, in ONE short sentence, tell the user what changed: "
                     f'"{explain}"') if explain else ""
    summary = (f"{verb} the email{detail}. It's shown as a card — give a brief,"
               " natural one-line note (never mention tools or modes)."
               + guidance_hint
               + (f" Quality read you may mention if useful: {note}." if note else ""))

    # Keep a bounded history of prior drafts so the user can compare revisions.
    updates = {"email": email, "style_profile": profile}
    hist = list(conversation.workspace.get("email_history") or [])
    if prev_body and current:
        hist.append({"subject": current.get("subject"), "body": prev_body})
        updates["email_history"] = hist[-10:]
    return ToolResult(summary=summary, message=card, workspace_updates=updates)


def _quality_note(quality) -> str:
    """A short human quality line from the deterministic report (or '')."""
    if not isinstance(quality, dict):
        return ""
    return (f"{quality.get('overall')}/100 overall, hook {quality.get('hook')}, "
            f"reply-likelihood {quality.get('reply_likelihood')}, "
            f"spam risk {quality.get('spam_risk')}, "
            f"~{quality.get('reading_seconds')}s read")


def _do_subjects(source, conversation, count) -> ToolResult:
    current = conversation.workspace.get("email")
    current = current if (current and current.get("status") == "ok") else None
    try:
        result = write_subject_lines(source, current_email=current,
                                     count=int(count) if count else 5)
    except Exception:  # noqa: BLE001
        return ToolResult(summary="Subject lines couldn't be generated just now.")
    if result.get("status") != "ok" or not result.get("subjects"):
        return ToolResult(summary="Could not generate subject lines: "
                          + (result.get("reason") or "no usable options") + ".")
    def stars(n):
        n = max(1, min(5, int(n or 3)))
        return "★" * n + "☆" * (5 - n)
    listed = "\n".join(
        f"  {stars(s.get('rating'))}  {s['text']}  — {s.get('reason') or s['style']}"
        for s in result["subjects"])
    return ToolResult(
        summary="Here are the ranked subject-line options (strongest first). Present "
                "them to the user as a short list, keeping each line's exact wording, "
                "the stars, and the short reason:\n" + listed,
        workspace_updates={"subject_options": result["subjects"]})


def _do_variations(source, conversation, count, allow_thin, guidance=None) -> ToolResult:
    try:
        result = write_variations(source, count=int(count) if count else 3,
                                  allow_thin=allow_thin, guidance=guidance,
                                  style_note=_style_note(conversation))
    except Exception:  # noqa: BLE001
        return ToolResult(summary="Variations couldn't be generated just now.")
    variations = result.get("variations") or []
    if result.get("status") != "ok" or not variations:
        return ToolResult(summary="Could not produce distinct variations: "
                          + (result.get("reason") or "no valid options") + ".")

    for v in variations:
        v["id"] = _variation_id(v["label"])      # stable slot id (version-a/-b/-c)
    messages = [_artifact_card(
        v["id"], f"Version {v['label']}.",
        subject=v["subject"], body=v["body"], company=result.get("company"),
        to=result.get("to"), label=f"Version {v['label']}", angle=v.get("angle"))
        for v in variations]
    # Version A becomes the "current" draft so a follow-up ("make it shorter")
    # revises it; all versions are kept for reference.
    first = variations[0]
    email_state = {"status": "ok", "id": "email", "subject": first["subject"],
                   "body": first["body"], "company": result.get("company"),
                   "to": result.get("to"), "used_reveal": False}
    labels = ", ".join(v["label"] for v in variations)
    return ToolResult(
        summary=(f"Regenerated the FULL set of {len(variations)} versions ({labels}) "
                 "— these replace any earlier versions (a variations run always "
                 "produces a fresh set; you cannot keep individual ones). Each is "
                 "shown as its own card. Tell the user this is a new set and how the "
                 "versions differ (one line each) using the angle labels; do NOT say "
                 "any version is 'unchanged' or 'same as before', and don't repeat "
                 "the full email text."),
        messages=messages,
        workspace_updates={"variations": variations, "email": email_state})


def _do_compare(conversation) -> ToolResult:
    """Show the previous draft vs the current one with a clean word-level diff.
    Pure presentation — no regeneration, no model call."""
    import difflib
    cur = conversation.workspace.get("email")
    cur = cur if (cur and cur.get("status") == "ok") else None
    hist = conversation.workspace.get("email_history") or []
    if not cur or not hist:
        return ToolResult(summary="There's only one version so far — nothing to "
                          "compare yet. Offer to revise it first.")
    prev = hist[-1]
    old, new = (prev.get("body") or ""), (cur.get("body") or "")
    added, removed = _word_diff(difflib, old, new)
    parts = [f"Previous subject: {prev.get('subject') or '(none)'}",
             f"Current subject:  {cur.get('subject') or '(none)'}",
             f"Length: {len(old.split())} → {len(new.split())} words."]
    if removed:
        parts.append("Removed: " + "; ".join(removed[:6]))
    if added:
        parts.append("Added: " + "; ".join(added[:6]))
    explain = _writer_review.explain_change(old, new)
    if explain:
        parts.append(explain)
    return ToolResult(
        summary=("Comparison of the last two versions (present this to the user "
                 "cleanly — previous vs current, then what changed):\n"
                 + "\n".join(parts)))


def _word_diff(difflib, old: str, new: str):
    """Short phrases added/removed between two drafts (readable, not a raw diff)."""
    sm = difflib.SequenceMatcher(a=old.split(), b=new.split())
    added, removed = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "delete"):
            frag = " ".join(old.split()[i1:i2]).strip()
            if len(frag) > 2:
                removed.append(frag[:60])
        if tag in ("replace", "insert"):
            frag = " ".join(new.split()[j1:j2]).strip()
            if len(frag) > 2:
                added.append(frag[:60])
    return added, removed


# ──────────────────────────────────────────────────────────────────────
#  Tool: outreach strategy — decide the next move (thinks; never writes/researches)
# ──────────────────────────────────────────────────────────────────────
def _strategy_summary(d) -> str:
    act = d.recommended_action
    lines = ["OUTREACH STRATEGY (internal guidance — present it naturally in your "
             "own words; NEVER mention tools, modes, scores-as-jargon, or that a "
             "'strategy agent' exists):",
             f"- Recommended move: {act}. Outreach confidence: {d.confidence}/100."]
    if d.primary_hook:
        lines.append(f'- Strongest hook to lead with: "{d.primary_hook}".')
    guide = {
        strategy.RESEARCH: "Tell the user you'd research the company first, and offer to do it now.",
        strategy.ENRICH: "Recommend deeper research to strengthen the angle before reaching out; offer to run it.",
        strategy.HOLD: "Advise AGAINST sending yet — there isn't enough specific, verifiable detail to personalise. Be honest; suggest researching more or a different prospect rather than forcing a generic email.",
        strategy.DRAFT: (f"Recommend drafting one sharp, personal email (voice: {d.recommended_persona}, "
                         f"channel: {d.recommended_channel}); offer to write it."),
        strategy.SEQUENCE: (f"Recommend a {d.recommended_sequence.get('steps')}-step "
                            f"{d.recommended_channel} sequence; offer to build it."),
    }.get(act, "Recommend the next step conversationally.")
    lines.append("- " + guide)
    if d.missing_information:
        lines.append("- Only if useful, note what would strengthen it: "
                     + ", ".join(d.missing_information[:4]) + ".")
    return "\n".join(lines)


def _tool_plan_outreach(inp: dict, conversation) -> ToolResult:
    decision = strategy.decide_from_workspace(conversation.workspace)
    return ToolResult(
        summary=_strategy_summary(decision),
        workspace_updates={"strategy": decision.to_dict()})


# ──────────────────────────────────────────────────────────────────────
#  Tool: lead qualification — is this lead worth pursuing? (judges; never
#  writes/researches; never picks outreach tactics — that's the strategy tool)
# ──────────────────────────────────────────────────────────────────────
def _qualification_summary(r) -> str:
    lines = ["LEAD QUALIFICATION (internal guidance — present it naturally in your "
             "own words; NEVER mention tools, scores-as-jargon, or that a "
             "'qualification agent' exists):",
             f"- Verdict: {r.recommendation} (fit: {r.fit_level}, priority: "
             f"{r.priority}, {r.qualification_score}/100, confidence {r.confidence}/100)."]
    guide = {
        qualification.HIGH_PRIORITY: "Tell the user this looks like a strong, in-market "
            "lead worth prioritising now. Mention the strongest reasons briefly.",
        qualification.CONTINUE: "Tell the user it's a reasonable lead worth pursuing. Keep it brief.",
        qualification.RESEARCH_MORE: "Be honest that there isn't enough to judge fit yet; "
            "offer to research the company more deeply first.",
        qualification.REJECT: "Gently advise this one probably isn't worth pursuing, and say "
            "why in one line (poor fit or a disqualifier). Don't be harsh; suggest focusing "
            "elsewhere.",
    }.get(r.recommendation, "Summarise the verdict conversationally.")
    lines.append("- " + guide)
    if r.strongest_signals:
        lines.append("- Strongest signals: " + "; ".join(r.strongest_signals[:3]) + ".")
    if r.disqualifiers:
        lines.append("- Disqualifiers: " + "; ".join(r.disqualifiers) + ".")
    if r.missing_information:
        lines.append("- If useful, note what's missing: "
                     + ", ".join(r.missing_information[:3]) + ".")
    lines.append("- Do NOT draft, research, or send anything from this tool; only convey the verdict.")
    return "\n".join(lines)


def _tool_qualify_lead(inp: dict, conversation) -> ToolResult:
    result = qualification.qualify_from_workspace(conversation.workspace)
    return ToolResult(
        summary=_qualification_summary(result),
        workspace_updates={"qualification": result.to_dict()})


# ──────────────────────────────────────────────────────────────────────
#  Tool: deliverability & cost guard — is it SAFE to send? (inspects only;
#  never writes, rewrites, researches, or sends)
# ──────────────────────────────────────────────────────────────────────
def _guard_input_from_workspace(ws: dict) -> dict:
    """Build the guard's input from what the thread already has (the current draft
    and any sequence). Missing sections are simply omitted — the guard treats
    absent data cautiously and never fabricates."""
    ws = ws or {}
    gi = {}
    email = ws.get("email")
    if email and email.get("status") == "ok" and email.get("body"):
        gi["email"] = {"subject": email.get("subject", ""), "body": email.get("body", ""),
                       "to": email.get("to", ""),
                       "company": email.get("company", "")}
    elif isinstance(email, dict) and email:
        gi["writer"] = {"status": email.get("status"), "reason": email.get("reason")}
        gi["email"] = {"subject": email.get("subject", ""), "body": email.get("body", ""),
                       "to": email.get("to", ""),
                       "company": email.get("company", "")}
    seq = ws.get("sequence")
    if isinstance(seq, list) and seq:
        gi["sequence"] = {
            "prior_bodies": [e.get("body") for e in seq if e.get("body")],
            "spacing_days": [e.get("delay_days") for e in seq
                             if e.get("delay_days") is not None],
        }
    research = ws.get("research")
    if isinstance(research, dict) and isinstance(research.get("data"), dict):
        gi["research"] = {"company_name": research["data"].get("company_name")}
    for section in ("usage", "campaign", "mailbox", "prospect", "personalization",
                    "auth", "qualification", "strategy"):
        if isinstance(ws.get(section), dict) and ws[section]:
            gi[section] = ws[section]
    return gi


def _guard_summary(res: dict) -> str:
    cost = res.get("cost", {})
    deliv = res.get("deliverability", {})
    issues = (deliv.get("issues", []) + cost.get("issues", []))[:4]
    fixes = (deliv.get("recommendations", []) + cost.get("recommendations", []))[:3]
    lines = ["SEND SAFETY CHECK (internal guidance — present it naturally; NEVER "
             "mention tools, scores-as-jargon, or a 'guard'):",
             f"- Verdict: {res.get('decision')} (risk {res.get('overallRisk')}/100)."]
    guide = {
        "BLOCK": "Tell the user this should NOT be sent as-is and why (top reasons), "
                 "plainly. Do NOT send it. Suggest they address the issues first.",
        "WARN": "Let the user know it's sendable but has some deliverability risks worth "
                "tightening; mention the top one or two. Do not alarm them.",
        "ALLOW": "Reassure the user it looks safe to send.",
    }.get(res.get("decision"), "Summarise the safety verdict conversationally.")
    lines.append("- " + guide)
    if issues:
        lines.append("- Issues: " + "; ".join(issues) + ".")
    if fixes:
        lines.append("- Fixes to suggest: " + "; ".join(fixes) + ".")
    lines.append("- Do NOT rewrite the email or research anything from this tool; only convey the verdict.")
    return "\n".join(lines)


def _guard_input(conversation) -> dict:
    """The guard's input: the thread's draft/sequence enriched with LIVE production
    data (real send history for this user) so the verdict reflects reality — e.g. it
    blocks re-contacting a prospect who already replied. Read-only; never fabricates
    (falls back to workspace-only if live data can't be read)."""
    base = _guard_input_from_workspace(conversation.workspace)
    user_id = getattr(conversation, "_user_id", None)
    if not user_id:
        return base
    try:
        from guard.context import build_context
        live = build_context(user_id, email=base.get("email"),
                             sequence=base.get("sequence"),
                             recipients=base.get("recipients"))
    except Exception:  # noqa: BLE001 - a data-layer hiccup must never break the check
        return base
    merged = dict(base)
    for key, value in live.items():
        if key == "email":
            continue                       # keep the thread's current draft verbatim
        merged[key] = value                # live prospect/mailbox/sequence win
    return merged


def _tool_guard_check(inp: dict, conversation) -> ToolResult:
    result = _guard_assess(_guard_input(conversation))
    return ToolResult(summary=_guard_summary(result),
                      workspace_updates={"guard": result})


def _tool_send_email(inp: dict, conversation) -> ToolResult:
    """Actually SEND the current drafted email via the user's connected Gmail.

    Runs through the automation engine (encrypted per-user token, idempotent send,
    recorded as a workflow so it also appears on the automation dashboard). Honest
    on every branch — it never reports success unless Gmail accepted the message,
    and it tells the agent to prompt for a connection / recipient when needed.
    """
    ws = conversation.workspace
    email = ws.get("email")
    if not (email and email.get("status") == "ok" and email.get("body")):
        return ToolResult(summary="There's no finished email on file to send. Offer to "
                          "draft one first; do NOT claim anything was sent.")
    to = str(inp.get("to") or email.get("to") or "").strip()
    if "@" not in to or len(to) > 254:
        return ToolResult(summary="You don't have the recipient's email address yet. Ask "
                          "the user for the exact address to send to, then send. Do NOT "
                          "invent an address or claim it was sent.")
    user_id = getattr(conversation, "_user_id", None)
    if not user_id:
        return ToolResult(summary="Couldn't confirm the signed-in account, so nothing was "
                          "sent. Ask the user to retry from the workspace.")

    # Deliverability & cost guard — a dangerous email is never sent. Read-only; it
    # only inspects and scores, now against LIVE send history (so it also blocks
    # re-contacting a prospect who already replied/bounced). A BLOCK stops the send.
    guard = _guard_assess(_guard_input(conversation))
    if guard.get("decision") == "BLOCK":
        reasons = (guard.get("deliverability", {}).get("issues", [])
                   + guard.get("cost", {}).get("issues", []))[:3]
        _tele_event("email", "guard_blocked", user_id=user_id, entity_id=to,
                    detail="; ".join(reasons)[:200])
        return ToolResult(
            summary=("The email was NOT sent — a send-safety check flagged it as risky "
                     f"({'; '.join(reasons) or 'high deliverability risk'}). Tell the user "
                     "plainly why it was held and suggest they tighten the copy before "
                     "sending. Do NOT claim it was sent."),
            workspace_updates={"guard": guard})

    try:
        from automation import engine as _eng, states as _st, tokens as _atok
        from automation.store import WorkflowStore
    except Exception:  # noqa: BLE001 - automation layer unavailable
        return ToolResult(summary="Sending isn't available in this environment right now. "
                          "Be honest; do not claim it was sent.")
    if not _atok.default_store().valid_access_token(user_id, "gmail"):
        return ToolResult(summary="Gmail isn't connected for this account, so the email was "
                          "NOT sent. Tell the user to connect Gmail on the Connections page "
                          "(/connections.html), then ask you to send again.")
    try:
        store = WorkflowStore()
        wf = _eng.create_workflow(
            store, user_id,
            [{"subject": email.get("subject", ""), "body": email["body"], "to": to,
              "artifact_id": email.get("id", "email")}],
            company=ws.get("company", ""), to_email=to, provider="gmail")
        _eng.advance_workflow(wf, store, credentials_provider=_atok.credentials_provider)
        wf = store.load(wf.id, user_id=user_id)
    except Exception:  # noqa: BLE001 - never surface a trace to the user
        return ToolResult(summary="The send failed unexpectedly. Tell the user honestly; do "
                          "not claim success.")
    if wf and wf.state in (_st.SENT, _st.WAITING, _st.COMPLETED):
        return ToolResult(
            summary=f"The email was really sent to {to} via the user's connected Gmail. "
                    "Confirm briefly and naturally that it's on its way.",
            workspace_updates={"last_send": {"to": to, "provider": "gmail",
                                             "workflow_id": wf.id, "state": wf.state}})
    reason = (wf.last_error if wf else "") or "the send didn't complete"
    return ToolResult(summary=f"The email was NOT sent — {reason}. Tell the user honestly and "
                      "suggest reconnecting Gmail on the Connections page. Do NOT claim success.")


# ──────────────────────────────────────────────────────────────────────
#  Roadmap stubs — real registered tools so the agent can defer gracefully.
#  Replace a handler with a working one to ship the capability; nothing else
#  in the chat layer changes.
# ──────────────────────────────────────────────────────────────────────
def _coming_soon(capability: str):
    def handler(inp: dict, conversation) -> ToolResult:
        return ToolResult(
            summary=(f"The '{capability}' capability is not available yet — it is "
                     "on the roadmap. Tell the user it's coming soon; do not "
                     "pretend it happened."))
    return handler


# ──────────────────────────────────────────────────────────────────────
#  Tool: find prospects (Prospect Discovery Agent — discovers companies)
# ──────────────────────────────────────────────────────────────────────
def _tool_find_prospects(inp: dict, conversation) -> ToolResult:
    """Discover companies matching an ICP. Deterministic (Tavily+Exa); it does
    NOT research, qualify, or write — it hands leads to the rest of the pipeline.
    Per-user dedupe means "find another N" naturally returns new companies."""
    owner = getattr(conversation, "_user_id", None) or conversation.id
    q = DiscoveryQuery(
        industry=str(inp.get("industry") or ""),
        location=str(inp.get("location") or ""),
        employee_range=str(inp.get("employee_range") or ""),
        funding_stage=str(inp.get("funding_stage") or ""),
        keywords=inp.get("keywords") or [],
        exclude_keywords=inp.get("exclude_keywords") or [],
        raw=str(inp.get("query") or ""),
        limit=inp.get("limit") or 20,
    )
    # Skip the company currently being worked on in this thread (already researched).
    exclude = []
    url = conversation.workspace.get("company_url")
    if url:
        exclude.append(url)

    try:
        result = discovery_engine.discover(owner, q, exclude_domains=exclude)
    except Exception:  # noqa: BLE001 - discovery is designed not to raise
        return ToolResult(summary="Prospect discovery couldn't run just now.")

    if result.status == "error":
        return ToolResult(summary="Couldn't discover prospects: " + result.reason)
    if result.status == "empty":
        return ToolResult(summary=result.reason + " Tell the user plainly.",
                          workspace_updates={"prospects_last": []})

    lines = []
    for p in result.prospects:
        bits = [p.company_name, p.website]
        meta = [x for x in (p.industry, p.location, p.estimated_stage) if x and x != "unknown"]
        if meta:
            bits.append(", ".join(meta))
        lines.append(f"  - {' — '.join(bits)}  ({p.why_it_matches})")
    more = " There are more available if they ask for another batch." if result.has_more else ""
    return ToolResult(
        summary=(f"Discovered {result.returned} companies matching the ICP. Present "
                 "them to the user as a clean numbered list (company — website — a "
                 "few words on why it matches). Then offer to research any of them, "
                 "or find more." + more + "\n" + "\n".join(lines)),
        workspace_updates={"prospects_last": [p.public() for p in result.prospects]})


# ──────────────────────────────────────────────────────────────────────
#  Tool: research prospects (chat-directed Discovery -> Research -> Qualify)
#  A NEW ENTRY POINT into the existing pipeline: turn a plain-language ask into
#  a scored, browsable list, valuable on its own — no campaign/email required.
# ──────────────────────────────────────────────────────────────────────
def _tool_research_prospects(inp: dict, conversation) -> ToolResult:
    user_id = getattr(conversation, "_user_id", None)
    owner = user_id or conversation.id
    companies = [c for c in (inp.get("companies") or []) if str(c).strip()]
    query = str(inp.get("query") or "").strip()
    limit = inp.get("limit")

    has_more = False
    if companies:
        leads = companies
    elif query:
        status, leads, reason, has_more = research_pipeline.discover_leads(
            owner, query, limit=limit)
        if status == "error":
            return ToolResult(summary="Couldn't find prospects: " + reason)
        if status == "empty" or not leads:
            return ToolResult(summary=(reason or "No matching companies found.")
                              + " Tell the user plainly and offer to adjust the criteria.")
    else:
        return ToolResult(summary="Ask the user WHO to find (an ICP like 'B2B SaaS "
                          "founders hiring an SDR') or WHICH companies to evaluate "
                          "(a list of names or websites).")

    icp = conversation.workspace.get("icp")
    result = research_pipeline.research_and_qualify(
        leads, icp=icp, limit=limit, user_id=user_id)
    if result.get("status") != "ok" or not result.get("prospects"):
        return ToolResult(summary=(result.get("reason")
                          or "Couldn't research those prospects just now."))

    prospects = result["prospects"]
    card = Message(role="assistant", kind=PROSPECTS,
                   content=f"Researched and scored {result['researched']} of "
                           f"{result['count']} companies.",
                   data={"prospects": prospects, "summary": result["summary"],
                         "run_id": result["run_id"]})
    lines = [f"  {i}. {e['company']} — {e['score']}/100 "
             f"({e['recommendation'] or e['status']}) — {e['preview']}"
             for i, e in enumerate(prospects, 1)]
    more = " More candidates are available for a bigger batch." if has_more else ""
    return ToolResult(
        summary=("Researched a scored prospect list (shown as an interactive card: "
                 "each row is a one-line preview that expands to the full research "
                 "trail, sources, and score reasoning). Present it as a ranked list "
                 "using ONLY the previews below — do NOT paste the full research, the "
                 "card already holds it. Call out the top one or two, and offer to "
                 "expand any, or draft an email / X reply / Reddit / HN / contact-form "
                 "message for one. Nothing sends or posts automatically." + more
                 + "\n" + "\n".join(lines)),
        message=card,
        workspace_updates={"prospects_researched": {
            "run_id": result["run_id"], "prospects": prospects,
            "summary": result["summary"]}})


# ──────────────────────────────────────────────────────────────────────
#  Tool: draft a SAFE-CHANNEL message (X/Reddit/HN public reply, contact form).
#  Produces a DRAFT only — it never posts. Every draft goes through the shared
#  AI-voice detector + the (channel-aware) Guard before being shown as ready.
# ──────────────────────────────────────────────────────────────────────
def _tool_draft_channel(inp: dict, conversation) -> ToolResult:
    channel = str(inp.get("channel") or "").strip().lower()
    if channel not in channels.CHANNELS:
        return ToolResult(summary="Ask the user which channel this is for: an X "
                          "reply, a Reddit comment, an HN / Indie Hackers reply, or "
                          "a contact-form message.")
    context = str(inp.get("context") or "").strip() or None
    guidance = str(inp.get("guidance") or "").strip() or None
    data = _channel_research_data(conversation, inp.get("company"))

    try:
        result = channels.draft(channel, context=context, research_data=data,
                                guidance=guidance)
    except Exception:  # noqa: BLE001 - channels.draft shouldn't raise, but be safe
        return ToolResult(summary=f"The {channel} draft couldn't be generated just now.")

    status = result.get("status")
    label = result.get("label") or channel
    if status == "skip":
        return ToolResult(summary=result.get("reason") or f"Need more to draft the {label}.")
    if status == "error":
        return ToolResult(summary=f"Couldn't draft the {label}: "
                          + (result.get("reason") or "unknown reason") + ".")
    if status == "needs_review":
        issues = "; ".join((result.get("problems") or [])[:3])
        return ToolResult(
            summary=(f"Drafted a {label}, but it still trips a quality/safety check, "
                     "so it is NOT ready to post. Tell the user plainly and offer to "
                     "tighten it" + (f" (issues: {issues})" if issues else "") + "."),
            workspace_updates={"channel_draft": result})

    card = Message(role="assistant", kind=CHANNEL,
                   content=f"Drafted a {label} ({result.get('char_count')} chars).",
                   data={"channel": channel, "label": label,
                         "body": result.get("body"),
                         "char_count": result.get("char_count"),
                         "company": result.get("company"), "posted": False,
                         "guard": (result.get("guard") or {}).get("decision")})
    return ToolResult(
        summary=(f"Drafted a {label} (shown as a card); it passed the AI-voice and "
                 "send-safety checks. Give a one-line note, do NOT paste the whole "
                 "draft. Make clear it's a DRAFT the user posts MANUALLY — nothing is "
                 "posted automatically from here."),
        message=card, workspace_updates={"channel_draft": result})


def _channel_research_data(conversation, company) -> dict:
    """Best available research to ground a channel draft: the current thread's
    research, else a researched prospect matching `company`, else just the name."""
    ws = conversation.workspace
    research = ws.get("research")
    if isinstance(research, dict) and isinstance(research.get("data"), dict):
        return research["data"]
    label = str(company or "").strip().lower()
    pr = ws.get("prospects_researched") or {}
    for e in pr.get("prospects", []):
        if label and label in str(e.get("company", "")).lower():
            hook = next((f.get("value") for f in e.get("detail", {}).get("findings", [])
                         if f.get("label") == "Hook"), "")
            return {"company_name": e.get("company"),
                    "what_they_do": (e.get("detail") or {}).get("what_they_do"),
                    "unique_hook": hook}
    return {"company_name": company} if company else {}


REGISTRY = {}


def register(tool: Tool) -> None:
    REGISTRY[tool.name] = tool


register(Tool(
    name="resolve_company",
    description=(
        "Resolve a company NAME to its official website using web search, BEFORE "
        "researching. Call this first when the user gives a company name (e.g. "
        "'Stripe', 'Clay', 'Cursor') rather than a URL. If the user already gave a "
        "URL or domain, you can skip this and research directly. If it returns "
        "several possible matches, ask the user which one they mean before "
        "researching. The resolved website is cached for the thread."),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "Company name (or URL) the user gave."},
        },
        "required": ["query"],
    },
    handler=_tool_resolve_company,
))

register(Tool(
    name="research_company",
    description=(
        "Research ONE company from its website: crawl the site and extract "
        "verified facts (what they do, who they serve, positioning, hooks, team). "
        "Prefer passing a URL (resolve a bare name with resolve_company first). "
        "Call this when the thread has no research yet, or when the user explicitly "
        "asks for information the research on file does not contain. Do NOT call it "
        "for a company you already have research for unless refresh=true."),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "Company website URL or company name."},
            "refresh": {"type": "boolean",
                        "description": "Re-run research even if some is on file."},
            "find_founder": {"type": "boolean",
                             "description": "Also run the deeper founder/team name-hunt."},
        },
        "required": ["query"],
    },
    handler=_tool_research,
))

register(Tool(
    name="deep_research",
    description=(
        "Gather MULTI-SOURCE intelligence on a company and return a grounded, "
        "cited briefing with recent developments and ranked personalization "
        "hooks. It combines the company's own website, recent news (launches, "
        "funding, partnerships), and long-form/founder/technical content, then "
        "de-duplicates and ranks the findings. Use this when the user wants to "
        "UNDERSTAND a company, asks what's new/recent, or wants a strong, unique "
        "personalization angle. Pass `focus` describing what they care about "
        "(e.g. 'recent launches', 'founder background', 'a unique hook') so it "
        "queries the right sources. For grounding a cold email's facts, prefer "
        "research_company; this is for richer, current context and hooks."),
    input_schema={
        "type": "object",
        "properties": {
            "company": {"type": "string",
                        "description": "Company name or website URL. Omit to use "
                                       "the company already in this thread."},
            "focus": {"type": "string",
                      "description": "What the user wants to learn — drives which "
                                     "sources are queried (news, long-form, etc.)."},
        },
        "required": [],
    },
    handler=_tool_deep_research,
))

register(Tool(
    name="write_email",
    description=(
        "Write, revise, or vary the personalized cold email from what's on file "
        "(website research AND any multi-source intel). It does NOT research "
        "again — reuse existing context. Modes:\n"
        "- mode='draft' (default): write/revise one email. Pass `guidance` to "
        "revise the current draft (e.g. 'make it shorter', 'more founder-like', "
        "'target the CTO', 'rewrite only the CTA', 'rewrite the opening', 'warmer', "
        "'more direct', 'more technical', 'improve the hook'). Omit guidance for a "
        "first draft. Revisions modify the EXISTING email, never start over.\n"
        "- mode='subjects': generate five subject lines in different styles.\n"
        "- mode='variations': generate a FRESH SET of genuinely different versions "
        "(A curiosity-driven, B authority-driven, C problem-first). It ALWAYS "
        "regenerates the whole set. If the user dislikes one or wants a different "
        "mix, pass `guidance` describing the new set plus `count` (2 or 3).\n"
        "- mode='follow_up': write a follow-up to the email already on file (for "
        "'they didn't reply', 'write a follow-up'). Not a new first email.\n"
        "- mode='sequence': write a multi-step outbound sequence (for 'write a "
        "4-email sequence'); pass `count` (2-6, default 4). Each step differs.\n"
        "- mode='critique': score an email the user pasted (pass it as `email_text`) "
        "or the current draft, and suggest improvements. Use when the user asks "
        "'what do you think of this email?'.\n"
        "- mode='compare': show the previous draft vs the current one and what "
        "changed (for 'compare', 'show the difference'). No regeneration.\n"
        "Use this whenever the user wants email copy — do not write the email in "
        "your own prose; this tool produces it and it's shown as a card."),
    input_schema={
        "type": "object",
        "properties": {
            "mode": {"type": "string",
                     "enum": ["draft", "subjects", "variations", "follow_up",
                              "sequence", "critique", "compare"],
                     "description": "What to produce. Default 'draft'."},
            "guidance": {"type": "string",
                         "description": "For mode='draft': how to change the email "
                                        "(e.g. 'shorter', 'rewrite only the CTA'). "
                                        "Omit for a first draft."},
            "count": {"type": "integer",
                      "description": "For mode='variations' (2-3) or 'subjects' (default 5)."},
            "email_text": {"type": "string",
                           "description": "For mode='critique': the email the user "
                                          "pasted to be critiqued."},
        },
        "required": [],
    },
    handler=_tool_write_email,
))

register(Tool(
    name="plan_outreach",
    description=(
        "Decide the best outreach STRATEGY for the company on file: whether to "
        "reach out at all, whether more research is needed, the strongest hook to "
        "lead with, the channel, the sender voice, and whether a single email or a "
        "multi-step sequence fits — plus an outreach-confidence read. It does NOT "
        "write or research; it recommends the next move, then you use the other "
        "tools to execute it. Call this when the user asks strategic questions "
        "('should I reach out?', 'what's the best approach?', 'is this worth it?', "
        "'who should I contact?', 'should I do a sequence?') or when you need to "
        "decide the next step. Reuses what's already on file; never fabricates."),
    input_schema={"type": "object", "properties": {}, "required": []},
    handler=_tool_plan_outreach,
))

register(Tool(
    name="qualify_lead",
    description=(
        "Judge whether the company on file is a lead WORTH PURSUING — its ICP fit, "
        "buying intent, any disqualifiers, and whether the research is too thin to "
        "judge — and give a verdict: reject, research_more, continue, or "
        "high_priority. This is about WHETHER to pursue the lead (qualification), "
        "NOT how to reach out (that's plan_outreach). It does NOT write, research, "
        "or send anything. Call this when the user asks 'is this a good lead?', "
        "'is this company a fit?', 'should I bother with them?', 'qualify this "
        "lead', or 'is this worth my time?'. Reuses what's already on file; never "
        "fabricates."),
    input_schema={"type": "object", "properties": {}, "required": []},
    handler=_tool_qualify_lead,
))

register(Tool(
    name="guard_check",
    description=(
        "Check whether the current email/sequence is SAFE to send — deliverability "
        "(spam risk, AI-sounding copy, weak personalization, formatting, links) and, "
        "if usage data is on file, API-cost risk. Returns a verdict: ALLOW, WARN, or "
        "BLOCK, with the reasons. It ONLY inspects and scores — it never rewrites the "
        "email, researches, or sends. Call this when the user asks 'is this safe to "
        "send?', 'will this land in spam?', 'is this too spammy?', or before sending "
        "when you're unsure. (send_email already runs this gate automatically.)"),
    input_schema={"type": "object", "properties": {}, "required": []},
    handler=_tool_guard_check,
))

register(Tool(
    name="send_email",
    description=(
        "SEND the current drafted email to the prospect via the user's connected "
        "Gmail. Call ONLY when the user explicitly asks to send it (e.g. 'send it', "
        "'send the email', 'send it to john@acme.com'). Pass `to` if the user gives "
        "a recipient address; otherwise the address on the draft is used, and if "
        "none is known you'll be told to ask for it. Requires a connected Gmail "
        "account. NEVER tell the user an email was sent unless this tool confirms it."),
    input_schema={"type": "object", "properties": {
        "to": {"type": "string",
               "description": "Recipient email address, if the user provided one."}},
        "required": []},
    handler=_tool_send_email))

register(Tool(
    name="find_prospects",
    description=(
        "DISCOVER companies that match an ideal-customer profile (ICP), when the "
        "user doesn't have a specific company yet — e.g. 'find B2B SaaS companies "
        "in Canada', 'find Series A fintech startups', 'find Shopify apps under 50 "
        "people', 'find another 20', 'only SaaS ones'. Extract the filters from "
        "their request and pass them. It finds companies only — it does NOT "
        "research, qualify, or write; offer those as next steps. Calling again "
        "returns NEW companies (already-shown ones are skipped automatically), so "
        "'find another 20' just calls this again. Use `query` for the raw phrasing "
        "if filters are hard to separate."),
    input_schema={
        "type": "object",
        "properties": {
            "industry": {"type": "string",
                         "description": "e.g. 'B2B SaaS', 'fintech', 'devtools'."},
            "location": {"type": "string", "description": "e.g. 'Canada', 'NYC'."},
            "employee_range": {"type": "string",
                               "description": "e.g. '<50', '50-200', '200+'."},
            "funding_stage": {"type": "string",
                              "description": "e.g. 'seed', 'series a', 'bootstrapped'."},
            "keywords": {"type": "array", "items": {"type": "string"},
                         "description": "Must-have signals, e.g. ['hiring SDRs','AI']."},
            "exclude_keywords": {"type": "array", "items": {"type": "string"},
                                 "description": "Signals to exclude."},
            "limit": {"type": "integer", "description": "How many to return (default 20)."},
            "query": {"type": "string",
                      "description": "The user's raw request, if filters are unclear."},
        },
        "required": [],
    },
    handler=_tool_find_prospects,
))

register(Tool(
    name="research_prospects",
    description=(
        "Find AND evaluate prospects in one step, straight from a plain-language "
        "ask, returning a SCORED, browsable list (research summary + fit score + the "
        "reason for it, per company). Use this when the user wants a shortlist "
        "without starting a campaign — e.g. 'find SaaS founders who posted about "
        "hiring an SDR and tell me who's worth it', or 'here's my list of 20 "
        "companies, which are worth pursuing?'. Pass `query` for an ICP to DISCOVER "
        "companies, OR `companies` (names/websites) to evaluate a list the user "
        "already has. It runs Discovery -> Research -> Qualification (reusing those "
        "agents) and returns a card; it does NOT write or send anything. Bounded per "
        "run — offer to run more if they want a bigger batch. Prefer this over "
        "find_prospects when the user wants the companies SCORED/evaluated, not just "
        "listed."),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "ICP / who to find, e.g. 'B2B SaaS founders "
                                     "hiring an SDR this week'. Use for discovery."},
            "companies": {"type": "array", "items": {"type": "string"},
                          "description": "A list of company names or websites the "
                                         "user already has, to research + score."},
            "limit": {"type": "integer",
                      "description": "How many to research this run (bounded)."},
        },
        "required": [],
    },
    handler=_tool_research_prospects,
))

register(Tool(
    name="draft_channel_message",
    description=(
        "Draft a PUBLIC-channel message (never a DM): an X (Twitter) reply, a Reddit "
        "comment, a Hacker News / Indie Hackers reply, or a website contact-form "
        "message. Use when the user wants to respond to a specific post/thread or "
        "reach a company through its contact form instead of cold email. The reply "
        "channels (x_reply, reddit_comment, hn_reply) REQUIRE the target post/thread "
        "text in `context`. It writes in the right format/length for the channel and "
        "passes the same AI-voice + send-safety checks as email. It only DRAFTS — it "
        "never posts; the user posts manually. Pass `company` to ground it in a "
        "researched prospect."),
    input_schema={
        "type": "object",
        "properties": {
            "channel": {"type": "string",
                        "enum": ["x_reply", "reddit_comment", "hn_reply", "contact_form"],
                        "description": "Which public channel to draft for."},
            "context": {"type": "string",
                        "description": "The post/thread being replied to (required "
                                       "for x_reply, reddit_comment, hn_reply)."},
            "company": {"type": "string",
                        "description": "Company this is about, to reuse its research."},
            "guidance": {"type": "string",
                         "description": "Optional steer, e.g. 'more technical', 'shorter'."},
        },
        "required": ["channel"],
    },
    handler=_tool_draft_channel,
))

for _name, _desc in (
    ("handle_replies", "Read and draft responses to prospect replies."),
    ("linkedin_outreach", "Draft or send LinkedIn outreach to the prospect."),
):
    register(Tool(name=_name, description=_desc + " (Not available yet.)",
                  input_schema={"type": "object", "properties": {}, "required": []},
                  handler=_coming_soon(_name)))


def tool_specs() -> list:
    return [t.spec() for t in REGISTRY.values()]


def execute(name: str, tool_input: dict, conversation) -> ToolResult:
    tool = REGISTRY.get(name)
    if tool is None:
        return ToolResult(summary=f"Unknown tool '{name}'.")
    return tool.handler(tool_input or {}, conversation)
