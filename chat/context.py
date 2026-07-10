"""Compact text views of a thread's workspace for the model.

The full research result is large; the agent only needs a DIGEST to reason about
follow-ups and to know what is already known (so it reuses research instead of
re-crawling) and what is missing (so it only researches again on explicit
request). These helpers are shared by the tools and the system prompt.
"""


def research_digest(result: dict) -> str:
    """A short, model-friendly summary of a research result (or why there's none)."""
    if not result or result.get("status") == "error":
        return "No usable research yet."
    if result.get("status") == "skip":
        return ("Research ran but found too little to personalize: "
                + (result.get("reason") or "insufficient detail."))
    data = result.get("data") or {}
    lines = []

    def add(label, value):
        if value:
            lines.append(f"- {label}: {value}")

    add("Company", data.get("company_name"))
    add("What they do", data.get("what_they_do"))
    add("Target customer", data.get("target_customer"))
    add("Positioning",
        data.get("competitive_positioning") or data.get("product_category"))
    add("Business model", data.get("business_model"))
    add("Recent focus", data.get("recent_focus"))
    add("Mission", data.get("their_mission_or_why"))
    add("Metrics", data.get("metrics_or_traction"))
    add("Pricing", data.get("pricing_model"))

    contact = data.get("primary_contact_name") or data.get("founder_name")
    if contact:
        role = data.get("primary_contact_role") or data.get("founder_role") or "contact"
        add("Primary contact", f"{contact} ({role})")
    team = [m for m in (data.get("team_members") or []) if m.get("name")]
    if team:
        add("Team", ", ".join(f"{m['name']} ({m.get('role') or 'role n/a'})"
                              for m in team[:6]))
    if data.get("notable_customers"):
        add("Notable customers", ", ".join(data["notable_customers"][:6]))

    hooks = result.get("hooks") or []
    if hooks:
        lines.append("- Personalization hooks available:")
        for hook in hooks[:5]:
            lines.append(f"    * {hook.get('text')}")

    # What the research does NOT contain — so the agent knows what would require
    # a fresh research call if the user asks for it.
    missing_map = {
        "founder_name": "founder", "pricing_model": "pricing",
        "metrics_or_traction": "metrics", "notable_customers": "named customers",
        "recent_focus": "recent activity", "tech_stack": "tech stack",
    }
    missing = [label for key, label in missing_map.items() if not data.get(key)]
    if missing:
        add("NOT found in current research", ", ".join(missing))
    add("Research score", f"{result.get('research_score')}/100")
    return "\n".join(lines) if lines else "Research returned little specific detail."


def intel_digest(intel: dict) -> str:
    """A compact, model-friendly view of a multi-source research briefing so the
    agent can reference findings in its reply without re-researching."""
    if not intel or intel.get("status") == "error":
        return "No multi-source intel gathered yet."
    if intel.get("status") == "empty":
        return ("Multi-source research ran but the providers returned nothing "
                "usable for this company.")
    lines = []
    if intel.get("summary"):
        lines.append("Summary: " + intel["summary"])
    findings = intel.get("findings") or []
    if findings:
        lines.append("Key findings (most useful first, with sources):")
        for f in findings[:8]:
            tag = f.get("recency")
            rec = f" [{tag}]" if tag and tag != "unknown" else ""
            lines.append(f"    * {f.get('text')}{rec}  ({f.get('source_url')})")
    hooks = intel.get("hooks") or []
    if hooks:
        lines.append("Personalization hooks:")
        for h in hooks[:6]:
            lines.append(f"    * {h.get('text')}  ({h.get('source_url')})")
    used = intel.get("providers_used") or []
    if used:
        lines.append("Sources gathered via: " + ", ".join(used))
    return "\n".join(lines) if lines else "Intel returned little specific detail."


def email_digest(email: dict) -> str:
    if not email or email.get("status") != "ok":
        return "No email drafted yet."
    return f"Subject: {email.get('subject')}\n\n{email.get('body')}"


def workspace_state_text(workspace: dict) -> str:
    """The 'what's already known' block injected into the system prompt each turn."""
    research = (workspace or {}).get("research")
    email = (workspace or {}).get("email")
    parts = []

    if research and research.get("status") == "ok":
        parts.append(
            "RESEARCH ON FILE (reuse this — do NOT research again unless the user "
            "asks for something explicitly marked as NOT found below):\n"
            + research_digest(research))
    elif research and research.get("status") in ("skip", "error"):
        parts.append("Research was attempted but did not yield a usable lead: "
                     + (research.get("reason") or research.get("error") or ""))
    else:
        resolved = (workspace or {}).get("resolution") or {}
        if resolved.get("url"):
            parts.append("No research yet, but the company website is already "
                         f"resolved to {resolved['url']} (do NOT resolve again — "
                         "research that URL).")
        else:
            parts.append("No research on file yet for this thread.")

    intel = (workspace or {}).get("intel")
    if intel and intel.get("status") == "ok":
        parts.append(
            "MULTI-SOURCE INTEL ON FILE (reuse this — reference these findings and "
            "hooks directly; do NOT run deep_research again unless the user asks "
            "for something new):\n" + intel_digest(intel))

    if email and email.get("status") == "ok":
        parts.append("CURRENT EMAIL DRAFT (revise via write_email; it is already "
                     "shown to the user, don't paste it back):\n"
                     + email_digest(email))
    else:
        parts.append("No email drafted yet.")
    return "\n\n".join(parts)
