"""Chat agent: the tool-use loop that drives one user turn.

Given a conversation and the user's message, it runs Claude with the registered
tools, executes any tool calls (which read/write the thread's workspace and can
append rich cards), and lands on a short natural-language reply. It re-injects
the workspace state each turn (via the system prompt) so research/email carry
across turns WITHOUT replaying big tool payloads — that is what lets the agent
reuse prior research instead of re-crawling.

The loop is bounded (CHAT_MAX_TOOL_HOPS) and never raises for normal failures.
"""

import logging
import queue
import re
import threading

import telemetry
from config.settings import CHAT_HISTORY_MAX_TURNS, CHAT_MAX_TOOL_HOPS
from chat import style, tools
from chat.models import NOTICE, EMAIL, RESEARCH, TEXT
from chat.prompts import build_system_prompt
from services import claude_client

log = logging.getLogger("chat.agent")


def _noop(*_a, **_k):
    return None


# Human, present-tense labels for the tools that do NOT narrate their own progress
# (discovery does, via conversation._progress). These become the live "step" the
# UI shows instead of a spinner, so the user always knows what is happening.
_TOOL_LABEL = {
    "research_company": "Researching the company",
    "research_prospect": "Researching the company",
    "write_email": "Writing the draft",
    "qualify_lead": "Scoring the lead",
    "get_stats": "Pulling your numbers",
    "summarize_replies": "Reading your replies",
    "list_campaigns": "Checking your campaigns",
    "guard_check": "Running deliverability checks",
    "send_email": "Getting the email ready",
}
# Tools that stream their OWN granular stages through conversation._progress.
_PROGRESS_TOOLS = {"find_prospects", "research_prospects"}

# Compact stand-ins so replayed history stays small (the live email/research live
# in the system-prompt workspace state, not in every turn).
_PLACEHOLDER = {EMAIL: "(email drafted, shown to the user as a card)",
                RESEARCH: "(research completed, summary shown to the user)"}

# No em dashes in Saqua copy is a HARD rule, and the system prompt has said so for
# a while. A live turn still shipped one, so it is now enforced deterministically
# here rather than trusted to the model: the prompt asks, this guarantees.
_EM_DASH_SPACED = re.compile(r"\s+—\s+")


def _no_em_dashes(text):
    """Replace em dashes with the punctuation a human would have used."""
    if not text or "—" not in text:
        return text
    out = _EM_DASH_SPACED.sub(", ", text)      # "a — b" -> "a, b"
    out = out.replace("—", " ")            # any remaining, unspaced use
    out = re.sub(r" {2,}", " ", out)
    return re.sub(r"\s*[,]?\s*([,.;:!?])", r"\1", out)  # never leave " ." or ", ."


def respond(conversation, user_text: str, store=None, user_id=None):
    """Handle one user message end-to-end (blocking); returns the conversation.

    ``user_id`` (the verified owner) is attached transiently to the conversation
    object — NOT persisted to the workspace and never serialised to the client —
    so owner-scoped tools (e.g. send_email, which sends via that user's connected
    Gmail) know whose credentials to use.
    """
    _run_turn(conversation, user_text, store, user_id, _noop)
    return conversation


def respond_stream(conversation, user_text: str, store=None, user_id=None):
    """The SAME turn as ``respond``, but yields ``(event, data)`` tuples AS the work
    happens, for Server-Sent Events. The turn runs on a worker thread and pushes
    events onto a queue, so progress from deep inside a tool (the real discovery
    stages) and each card surface LIVE instead of all at once at the end.

    Events:
      ("step", {"label": str})            a real pipeline stage, in order
      ("thought", {"label": str})         WHY, derived from the run's real state
                                          (discovery/narration.py) - never canned
      ("message", {"message": <dict>})    a transcript message (card or prose)
      ("error", {"message": str})         a user-safe failure
      ("done", {})                        the turn is complete

    ``respond`` and ``respond_stream`` share ``_run_turn`` so the two paths can
    never drift; the blocking path just passes a no-op emitter.
    """
    events: "queue.Queue" = queue.Queue()
    sentinel = object()

    def emit(event, data=None):
        events.put((event, data or {}))

    def worker():
        try:
            _run_turn(conversation, user_text, store, user_id, emit)
        except Exception:  # noqa: BLE001 - a crash becomes a clean terminal event
            log.exception("streaming turn failed")
            emit("error", {"message": "Something went wrong. Please try again."})
        finally:
            events.put(sentinel)

    threading.Thread(target=worker, name="chat-turn", daemon=True).start()
    while True:
        item = events.get()
        if item is sentinel:
            break
        yield item
    yield ("done", {})


def _emit_message(conversation, emit, message):
    """Append a transcript message and stream it in one place, so the streamed
    transcript is exactly the persisted one (no drift between paths)."""
    conversation.add(message)
    emit("message", {"message": message.to_dict()})
    return message


def _emit_assistant(conversation, emit, text, kind=TEXT):
    from chat.models import Message
    return _emit_message(conversation, emit,
                         Message(role="assistant", content=text, kind=kind))


def _run_turn(conversation, user_text: str, store, user_id, emit):
    """One agent turn, shared by the blocking and streaming entry points. Mutates
    ``conversation`` and calls ``emit(event, data)`` at each streamable moment; the
    blocking path passes a no-op emitter, so behaviour is identical either way."""
    conversation._user_id = user_id
    conversation.add_user(user_text)

    # Hydrate the per-user writing-style profile so the writer already matches
    # how this user likes their emails (learned from past messages). Kept in the
    # workspace so the tools see it; persisted per-user (not per-thread) below.
    if store is not None and hasattr(store, "load_profile"):
        try:
            conversation.workspace["style_profile"] = store.load_profile()
        except Exception:  # noqa: BLE001 - never let profile IO break a reply
            pass
    # The user's own company details (set in Settings) — loaded fresh each turn so
    # edits take effect immediately and the agent works on their behalf in every
    # chat. Read-only here; it is written from the /api/company endpoint.
    if store is not None and hasattr(store, "load_company"):
        try:
            conversation.workspace["company_profile"] = store.load_company()
        except Exception:  # noqa: BLE001 - never let company IO break a reply
            pass
    # Free-tier usage (distinct prospects worked) — loaded so the research tools
    # can enforce the plan cap, persisted per-user (not per-thread) below.
    if store is not None and hasattr(store, "load_usage"):
        try:
            conversation.workspace["usage"] = store.load_usage()
        except Exception:  # noqa: BLE001 - never let usage IO break a reply
            pass
    # Learn from THIS message directly — a preference like "no emojis" or "never
    # say X" is captured whether or not it triggers a rewrite, so the user never
    # has to repeat it. style.learn only reacts to explicit style cues, so an
    # ordinary message ("research Stripe") changes nothing.
    try:
        conversation.workspace["style_profile"] = style.learn_from_guidance(
            conversation.workspace.get("style_profile") or style.default_profile(),
            user_text)
    except Exception:  # noqa: BLE001
        pass

    system = build_system_prompt(conversation.workspace)
    messages = _history_to_messages(conversation)

    # Tool-selection visibility: log the tools offered to the model for this turn
    # ONCE up front, so a later "was the tool available but not chosen?" question is
    # a log read, not a guess. The per-hop lines below then record what was chosen.
    specs = tools.tool_specs(user_id=user_id)
    log.info("tools offered (%d) for %r: %s", len(specs),
             (user_text or "")[:80], ", ".join(s["name"] for s in specs))

    emit("step", {"label": "Thinking"})
    for _hop in range(CHAT_MAX_TOOL_HOPS):
        try:
            # Attribute the orchestration LLM call to the "chat" agent (telemetry).
            with telemetry.scope(user_id=user_id, agent="chat"):
                resp = claude_client.call_with_tools(system, messages, specs)
        except claude_client.ClaudeClientError as exc:
            _emit_assistant(conversation, emit,
                            f"I hit a problem reaching the model: {exc}", kind=NOTICE)
            _save(store, conversation)
            return
        except Exception:  # noqa: BLE001 - last-resort guard; never crash the UI
            _emit_assistant(conversation, emit,
                            "Something went wrong. Please try again.", kind=NOTICE)
            _save(store, conversation)
            return

        if not resp["tool_uses"]:
            # No tool chosen — the model answered directly. Logged explicitly so
            # "tool offered but not selected" is distinguishable from "not offered".
            log.info("tool selection (hop %d): none - model answered directly", _hop)
            _emit_assistant(conversation, emit, _no_em_dashes(resp["text"]) or "Done.")
            break

        # The model narrated + decided to use tools: show the narration first.
        log.info("tool selection (hop %d): %s", _hop,
                 ", ".join(c["name"] for c in resp["tool_uses"]))
        if resp["text"]:
            _emit_assistant(conversation, emit, _no_em_dashes(resp["text"]))

        messages.append({"role": "assistant", "content": resp["assistant_content"]})
        results = []
        for call in resp["tool_uses"]:
            log.info("tool call: %s %s", call["name"], call["input"])
            _announce_tool(call["name"], emit)
            # Tools that stream their own stages get a live progress sink; the rest
            # already announced a single step above. Cleared after so a later tool
            # never inherits a stale sink. ``kind`` separates WHAT is happening
            # ("step") from WHY ("thought", grounded in discovery/narration.py).
            conversation._progress = (
                (lambda label, kind="step": emit(kind, {"label": label}))
                if call["name"] in _PROGRESS_TOOLS else None)
            # Record the agent execution + attribute any LLM calls inside the tool.
            try:
                with telemetry.track_agent(call["name"], user_id=user_id):
                    result = tools.execute(call["name"], call["input"], conversation)
            finally:
                conversation._progress = None
            if result.workspace_updates:
                conversation.workspace.update(result.workspace_updates)
            if result.message is not None:
                _emit_message(conversation, emit, result.message)
            for extra in getattr(result, "messages", None) or []:
                _emit_message(conversation, emit, extra)
            results.append({"type": "tool_result", "tool_use_id": call["id"],
                            "content": result.summary})
        messages.append({"role": "user", "content": results})
    else:
        # Ran out of tool hops without a final answer.
        _emit_assistant(
            conversation, emit,
            "I've done what I can for now, let me know how you'd like to proceed.",
            kind=NOTICE)

    _maybe_set_title(conversation, user_text)
    _save(store, conversation)


def _announce_tool(name: str, emit) -> None:
    """Emit the single step for a tool that does not narrate its own progress.
    Discovery/research tools stay silent here because they stream finer stages."""
    if name in _PROGRESS_TOOLS:
        emit("step", {"label": "Finding companies"})
        return
    label = _TOOL_LABEL.get(name)
    if label:
        emit("step", {"label": label})


# ──────────────────────────────────────────────────────────────────────
def _history_to_messages(conversation) -> list:
    """Prior transcript as Anthropic messages: compact, role-alternating, and
    starting with a user turn (API requirement)."""
    recent = conversation.messages[-CHAT_HISTORY_MAX_TURNS:]
    merged = []
    for msg in recent:
        text = _PLACEHOLDER.get(msg.kind, msg.content) if msg.role == "assistant" \
            else msg.content
        if not text:
            continue
        if merged and merged[-1]["role"] == msg.role:
            merged[-1]["content"] += "\n\n" + text     # coalesce same-role runs
        else:
            merged.append({"role": msg.role, "content": text})
    while merged and merged[0]["role"] != "user":       # must start with user
        merged.pop(0)
    return merged


def _maybe_set_title(conversation, user_text: str) -> None:
    if conversation.title not in ("", "New conversation"):
        return
    company = conversation.workspace.get("company")
    if company:
        conversation.title = company
    elif user_text and user_text.strip():
        conversation.title = user_text.strip()[:40]


def _save(store, conversation) -> None:
    if store is not None:
        try:
            store.save(conversation)
        except Exception:  # noqa: BLE001 - persistence must never break a reply
            log.warning("failed to persist conversation %s", conversation.id)
        # Persist the learned writing-style profile per-user (separate from the
        # thread) so preferences carry across every conversation.
        profile = (conversation.workspace or {}).get("style_profile")
        if profile and hasattr(store, "save_profile"):
            try:
                store.save_profile(profile)
            except Exception:  # noqa: BLE001
                log.warning("failed to persist style profile")
        # Persist the free-tier usage counter per-user (separate from the thread)
        # so the prospect cap carries across every conversation.
        usage = (conversation.workspace or {}).get("usage")
        if isinstance(usage, dict) and hasattr(store, "save_usage"):
            try:
                store.save_usage(usage)
            except Exception:  # noqa: BLE001
                log.warning("failed to persist usage")
