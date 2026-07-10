"""Outreach Strategy Agent — the decision-making brain of Saqua.

It does NOT write emails and it does NOT research. It *thinks*: given what the
other agents have already produced (a research result, optional multi-source
intel, an existing draft/sequence), it decides what should happen next —
whether to reach out at all, whether more research is needed, the strongest
hook, the channel, the persona voice, the sequence shape — and how confident it
is.

Design principles
-----------------
* **Standalone + decoupled.** Consumes only the OUTPUT SHAPES of the research
  engine and writer; it imports neither. That keeps the three agents cleanly
  separable and lets this one be tested in isolation.
* **Deterministic.** No model call. The same inputs always yield the same
  decision — fast, free, reproducible, and trivially testable. (An LLM adds
  nothing here; the reasoning is a function of grounded signals.)
* **Honest under uncertainty.** When there isn't enough verified detail to
  personalise, it recommends HOLD or more research rather than fabricating an
  angle. It never invents a hook.
* **Conversation-aware.** It considers what already exists in the thread (a
  draft, a sequence) when choosing the next move.
* **Structured + automation-ready.** Returns a ``StrategyDecision`` with a stable
  shape (``to_dict``) so a future automation layer can act on it without any
  redesign. The ``reasoning_summary`` explains the call internally; callers must
  present recommendations naturally and never expose these internals to users.
"""

from dataclasses import dataclass, field, asdict

# ── Recommended actions (the next strategic move) ──────────────────────
RESEARCH = "research"   # nothing usable yet — gather research first
ENRICH = "enrich"       # some grounding but low confidence — research deeper
DRAFT = "draft"         # enough to write one personalised email
SEQUENCE = "sequence"   # strong fit — a multi-step sequence is the right play
HOLD = "hold"           # NOT enough to personalise — do not send generic outreach

ACTIONS = (RESEARCH, ENRICH, DRAFT, SEQUENCE, HOLD)

# Persona = which sender VOICE to use downstream (maps to a WritingProfile kind).
PERSONAS = ("founder", "sales", "enterprise", "recruiting", "personal")

# ── Thresholds (deterministic, tunable) ────────────────────────────────
CONF_ENRICH_MAX = 45    # usable but below this -> recommend deeper research
CONF_HIGH = 80          # at/above this a multi-step sequence is warranted
_HOOKLESS_CAP = 30      # with no specific hook, confidence can't exceed this
_INTEL_ONLY_BASE = 35   # base confidence when only multi-source intel is on file


@dataclass
class StrategyDecision:
    recommended_action: str
    confidence: int
    primary_hook: str = None
    recommended_persona: str = "founder"
    recommended_channel: str = "email"
    recommended_sequence: dict = field(default_factory=lambda: {"type": "none", "steps": 0})
    reasoning_summary: str = ""
    missing_information: list = field(default_factory=list)
    signals: dict = field(default_factory=dict)   # transparency for tests/automation

    def to_dict(self) -> dict:
        return asdict(self)


# ── Signal extraction (from existing agents' outputs) ──────────────────
def _ranked_hooks(research, intel, data) -> list:
    """Strongest personalisation hooks first, de-duplicated. Research hooks are
    already ranked by the research engine; intel hooks augment them."""
    out = []
    if _ok(research):
        out += [h.get("text") for h in (research.get("hooks") or []) if h.get("text")]
        if not out and data.get("unique_hook"):
            out.append(data["unique_hook"])
    if _ok(intel):
        out += [h.get("text") for h in (intel.get("hooks") or []) if h.get("text")]
    seen, uniq = set(), []
    for t in out:
        if t and t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def _missing(data: dict) -> list:
    """High-value facts absent from the research — what would raise confidence."""
    checks = [
        (data.get("what_they_do"), "what the company does"),
        (data.get("target_customer") or data.get("industries_served"), "who they serve (ICP fit)"),
        (data.get("primary_contact_name") or data.get("founder_name"), "a decision-maker to address"),
        (data.get("recent_focus"), "a recent activity or trigger"),
        (data.get("metrics_or_traction"), "traction / metrics"),
    ]
    return [label for value, label in checks if not value]


def _persona(data: dict) -> str:
    """Which sender VOICE fits this prospect. Founder-to-founder is the default;
    a clearly enterprise prospect gets a more measured 'enterprise' voice."""
    blob = " ".join(str(data.get(k) or "") for k in
                    ("competitive_positioning", "what_they_do", "business_model",
                     "company_stage")).lower()
    if any(w in blob for w in ("enterprise", "fortune 500", "large organizations",
                               "large enterprises", "global 2000")):
        return "enterprise"
    return "founder"


def _ok(obj) -> bool:
    return isinstance(obj, dict) and obj.get("status") == "ok"


def _confidence(score, primary_hook, contact, target_fit, research_ok, n_hooks) -> int:
    if research_ok:
        c = int(score or 0)
        if primary_hook:
            c += 5
        if contact:
            c += 5
        if target_fit:
            c += 3
        c = min(100, c)
    else:  # intel-only (thin) — some confidence from multi-source hooks
        c = min(70, _INTEL_ONLY_BASE + 10 * max(0, n_hooks))
    if not primary_hook:
        c = min(c, _HOOKLESS_CAP)
    return max(0, c)


# ── The decision ───────────────────────────────────────────────────────
def _qualification_recommendation(qualification) -> str:
    if isinstance(qualification, dict):
        return str(qualification.get("recommendation") or "").strip().lower()
    return str(getattr(qualification, "recommendation", "") or "").strip().lower()


def decide(research=None, intel=None, email=None, sequence=None,
           qualification=None, override: bool = False) -> StrategyDecision:
    """Decide the next outreach move from what's already known. Never fabricates."""
    research_ok = _ok(research)
    research_skip = isinstance(research, dict) and research.get("status") == "skip"
    intel_ok = _ok(intel)
    data = (research.get("data") if research_ok else {}) or {}

    hooks = _ranked_hooks(research, intel, data)
    primary_hook = hooks[0] if hooks else None
    contact = data.get("primary_contact_name") or data.get("founder_name")
    target_fit = bool(data.get("target_customer") or data.get("industries_served"))
    has_draft = _ok(email)
    has_sequence = bool(sequence)
    persona = _persona(data)
    usable = research_ok or intel_ok
    source = "research" if research_ok else ("intel" if intel_ok else "none")

    signals = {
        "source": source, "research_score": int(research.get("research_score") or 0)
        if research_ok else None, "has_hook": bool(primary_hook), "hook_count": len(hooks),
        "has_contact": bool(contact), "target_fit": target_fit,
        "has_draft": has_draft, "has_sequence": has_sequence, "persona": persona,
    }

    qrec = _qualification_recommendation(qualification)
    signals["qualification"] = qrec or None
    if qrec == "reject" and not override:
        return StrategyDecision(
            recommended_action=HOLD, confidence=0, primary_hook=None,
            recommended_persona=persona, recommended_channel="email",
            recommended_sequence={"type": "none", "steps": 0},
            reasoning_summary="Qualification rejected this lead, so outreach is held.",
            missing_information=[], signals=signals)
    if qrec == "research_more" and not override:
        return StrategyDecision(
            recommended_action=ENRICH, confidence=0, primary_hook=primary_hook,
            recommended_persona=persona, recommended_channel="email",
            recommended_sequence={"type": "none", "steps": 0},
            reasoning_summary="Qualification says more research is needed before outreach.",
            missing_information=["enough verified research to qualify the lead"] + _missing(data),
            signals=signals)

    # ---- No usable information: gather research (never guess) ----
    if not usable and not research_skip:
        return StrategyDecision(
            recommended_action=RESEARCH, confidence=0, primary_hook=None,
            recommended_persona=persona, recommended_channel="email",
            recommended_sequence={"type": "none", "steps": 0},
            reasoning_summary="No usable research on file yet — gather it before "
                              "deciding on outreach.",
            missing_information=["company research"], signals=signals)

    # ---- Researched but too thin to personalise: do NOT send ----
    if research_skip and not intel_ok:
        return StrategyDecision(
            recommended_action=HOLD,
            confidence=min(_HOOKLESS_CAP, int(research.get("research_score") or 0)),
            primary_hook=None, recommended_persona=persona, recommended_channel="email",
            recommended_sequence={"type": "none", "steps": 0},
            reasoning_summary="Research ran but found too little specific, verifiable "
                              "detail to personalise — holding rather than sending "
                              "generic outreach.",
            missing_information=["a specific, verifiable hook"] + _missing(data),
            signals=signals)

    confidence = _confidence(signals["research_score"], primary_hook, contact,
                             target_fit, research_ok, len(hooks))
    missing = _missing(data)

    # ---- No specific hook: never send something generic ----
    if not primary_hook:
        return StrategyDecision(
            recommended_action=HOLD, confidence=confidence, primary_hook=None,
            recommended_persona=persona, recommended_channel="email",
            recommended_sequence={"type": "none", "steps": 0},
            reasoning_summary="No specific, verifiable hook to open on — holding "
                              "rather than sending something that reads generic.",
            missing_information=["a specific, verifiable hook"] + missing, signals=signals)

    # ---- Low confidence: research deeper before reaching out ----
    if confidence < CONF_ENRICH_MAX:
        return StrategyDecision(
            recommended_action=ENRICH, confidence=confidence, primary_hook=primary_hook,
            recommended_persona=persona, recommended_channel="email",
            recommended_sequence={"type": "none", "steps": 0},
            reasoning_summary=(f"Some grounding, but confidence is only {confidence}/100. "
                               "Deeper, multi-source research would sharpen the angle "
                               "before reaching out."),
            missing_information=missing, signals=signals)

    # ---- Enough to act: draft, or a sequence when the fit is strong ----
    if confidence >= CONF_HIGH and (persona == "enterprise" or len(hooks) >= 3):
        action, seq = SEQUENCE, ({"type": "standard", "steps": 4})
        why = ("Strong fit and multiple angles — a short multi-step sequence will "
               "outperform a single email.")
    elif has_draft:
        action, seq = SEQUENCE, ({"type": "short", "steps": 3})
        why = ("A first email already exists and the fit is solid — a light follow-up "
               "sequence is the natural next step.")
    else:
        action, seq = DRAFT, ({"type": "single", "steps": 1})
        why = "Enough verified detail and a strong hook — draft one sharp, personal email."

    return StrategyDecision(
        recommended_action=action, confidence=confidence, primary_hook=primary_hook,
        recommended_persona=persona, recommended_channel="email",
        recommended_sequence=seq,
        reasoning_summary=(f"Confidence {confidence}/100. Strongest hook: "
                           f"\"{primary_hook}\". {why}"),
        missing_information=missing, signals=signals)


def decide_from_workspace(workspace: dict) -> StrategyDecision:
    """Convenience: pull the agents' outputs from a chat thread's workspace and
    decide. Keeps the core ``decide`` decoupled from the workspace shape."""
    ws = workspace or {}
    return decide(research=ws.get("research"), intel=ws.get("intel"),
                  email=ws.get("email"), sequence=ws.get("sequence"),
                  qualification=ws.get("qualification"))
