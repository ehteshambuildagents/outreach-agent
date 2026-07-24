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

import telemetry
from config.settings import CHAT_HISTORY_MAX_TURNS, CHAT_MAX_TOOL_HOPS
from chat import style, tools
from chat.models import NOTICE, EMAIL, RESEARCH
from chat.prompts import build_system_prompt
from services import claude_client

log = logging.getLogger("chat.agent")

# Compact stand-ins so replayed history stays small (the live email/research live
# in the system-prompt workspace state, not in every turn).
_PLACEHOLDER = {EMAIL: "(email drafted — shown to the user as a card)",
                RESEARCH: "(research completed — summary shown to the user)"}


def respond(conversation, user_text: str, store=None, user_id=None):
    """Handle one user message end-to-end; returns the conversation.

    ``user_id`` (the verified owner) is attached transiently to the conversation
    object — NOT persisted to the workspace and never serialised to the client —
    so owner-scoped tools (e.g. send_email, which sends via that user's connected
    Gmail) know whose credentials to use.
    """
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

    for _hop in range(CHAT_MAX_TOOL_HOPS):
        try:
            # Attribute the orchestration LLM call to the "chat" agent (telemetry).
            with telemetry.scope(user_id=user_id, agent="chat"):
                resp = claude_client.call_with_tools(system, messages, specs)
        except claude_client.ClaudeClientError as exc:
            conversation.add_assistant(
                f"I hit a problem reaching the model: {exc}", kind=NOTICE)
            _save(store, conversation)
            return conversation
        except Exception:  # noqa: BLE001 - last-resort guard; never crash the UI
            conversation.add_assistant(
                "Something went wrong. Please try again.", kind=NOTICE)
            _save(store, conversation)
            return conversation

        if not resp["tool_uses"]:
            # No tool chosen — the model answered directly. Logged explicitly so
            # "tool offered but not selected" is distinguishable from "not offered".
            log.info("tool selection (hop %d): none - model answered directly", _hop)
            conversation.add_assistant(resp["text"] or "Done.")
            break

        # The model narrated + decided to use tools: show the narration first.
        log.info("tool selection (hop %d): %s", _hop,
                 ", ".join(c["name"] for c in resp["tool_uses"]))
        if resp["text"]:
            conversation.add_assistant(resp["text"])

        messages.append({"role": "assistant", "content": resp["assistant_content"]})
        results = []
        for call in resp["tool_uses"]:
            log.info("tool call: %s %s", call["name"], call["input"])
            # Record the agent execution + attribute any LLM calls inside the tool.
            with telemetry.track_agent(call["name"], user_id=user_id):
                result = tools.execute(call["name"], call["input"], conversation)
            if result.workspace_updates:
                conversation.workspace.update(result.workspace_updates)
            if result.message is not None:
                conversation.add(result.message)
            for extra in getattr(result, "messages", None) or []:
                conversation.add(extra)
            results.append({"type": "tool_result", "tool_use_id": call["id"],
                            "content": result.summary})
        messages.append({"role": "user", "content": results})
    else:
        # Ran out of tool hops without a final answer.
        conversation.add_assistant(
            "I've done what I can for now — let me know how you'd like to proceed.",
            kind=NOTICE)

    _maybe_set_title(conversation, user_text)
    _save(store, conversation)
    return conversation


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
