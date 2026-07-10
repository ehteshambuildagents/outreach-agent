"""Lead Qualification Agent — decides whether a lead is worth pursuing.

Given what the Research Agent already produced (a research result, optional
multi-source intel, and an optional ICP profile), it judges the *lead itself*:
company fit, ICP match, buying intent, disqualifiers, and whether the research is
too thin to judge — then recommends one of ``reject`` / ``research_more`` /
``continue`` / ``high_priority``.

How this differs from the Strategy Agent (no overlap)
-----------------------------------------------------
* Strategy answers **"how do we reach out?"** — hook, channel, persona, single
  email vs sequence. Qualification answers **"is this lead worth pursuing at
  all?"** — fit / intent / disqualifiers. It never chooses hooks, personas, or
  sequences, and its ``next_best_action`` stays coarse (pursue / skip / research),
  deferring the *tactics* of outreach to the Strategy Agent.

Design principles (same spine as the Strategy Agent)
----------------------------------------------------
* **Standalone + decoupled.** Consumes only the OUTPUT SHAPES of research/intel;
  imports no other agent.
* **Deterministic.** No model call — the judgement is a pure function of grounded
  signals, so it's fast, free, reproducible, and trivially testable. (An LLM adds
  nothing here and would only add cost + nondeterminism.)
* **Honest under uncertainty.** When the research is too thin to judge, it says
  ``research_more`` instead of guessing a fit.
* **Structured + automation-ready.** Returns a ``QualificationResult`` with a
  stable ``to_dict`` shape so a future automation layer can gate on it.
"""

from dataclasses import asdict, dataclass, field

# ── Recommendations (what to do with this lead) ────────────────────────
REJECT = "reject"                # poor fit or a hard disqualifier — don't pursue
RESEARCH_MORE = "research_more"  # too little to judge — gather more first
CONTINUE = "continue"            # a reasonable lead — worth pursuing normally
HIGH_PRIORITY = "high_priority"  # strong fit + active buying intent — pursue now

RECOMMENDATIONS = (REJECT, RESEARCH_MORE, CONTINUE, HIGH_PRIORITY)

# Fit levels + priority buckets (stable strings for the UI / automation).
FIT_STRONG, FIT_MODERATE, FIT_WEAK, FIT_UNKNOWN = "strong", "moderate", "weak", "unknown"
PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW, PRIORITY_NONE = "high", "medium", "low", "none"

# ── Thresholds (deterministic, tunable) ────────────────────────────────
QUALIFY_HIGH = 70        # score at/above this + buying intent -> high_priority
QUALIFY_CONTINUE = 45    # score at/above this -> continue
WEAK_RESEARCH_SCORE = 35 # research_score below this is "too thin to judge"

# Score component caps (sum to 100).
_FIT_MAX, _INTENT_MAX, _QUALITY_MAX, _REACH_MAX = 40, 30, 20, 10

# Recent, concrete triggers that indicate a company may be in-market to buy.
INTENT_KEYWORDS = (
    "hiring", "we're hiring", "open roles", "open positions", "raised", "funding",
    "series a", "series b", "series c", "seed round", "just raised", "new funding",
    "backed by", "launched", "launching", "new product", "expanding", "expansion",
    "scaling", "rapid growth", "migrating", "adopting", "rolling out", "partnership",
    "partnered", "acquired", "acquisition", "opening a", "record quarter",
    "doubled", "tripled",
)

# Conservative signals that a "company" is not a viable lead at all.
DISQUALIFIER_KEYWORDS = (
    "shutting down", "shut down", "ceased operations", "out of business",
    "no longer operating", "defunct", "acquired by", "for sale", "parked domain",
    "under construction", "coming soon", "site not found", "personal blog",
)


@dataclass
class QualificationResult:
    qualification_score: int
    fit_level: str
    priority: str
    recommendation: str
    confidence: int
    strongest_signals: list = field(default_factory=list)
    disqualifiers: list = field(default_factory=list)
    missing_information: list = field(default_factory=list)
    next_best_action: str = ""
    reasoning_summary: str = ""                      # internal only
    signals: dict = field(default_factory=dict)      # transparency for tests/automation

    def to_dict(self) -> dict:
        return asdict(self)


# ── Signal extraction ──────────────────────────────────────────────────
def _ok(obj) -> bool:
    return isinstance(obj, dict) and obj.get("status") == "ok"


def _blob(data: dict, intel=None) -> str:
    parts = [data.get(k) for k in (
        "what_they_do", "target_customer", "industries_served", "recent_focus",
        "metrics_or_traction", "competitive_positioning", "business_model",
        "company_stage", "tech_stack", "notable_customers", "unique_hook", "news")]
    if _ok(intel):
        parts += [h.get("text") for h in (intel.get("hooks") or [])]
        parts.append(intel.get("summary"))
    return " ".join(str(p or "") for p in parts).lower()


def _icp_match(blob: str, role: str, icp: dict):
    """Match the research against a supplied ICP. Returns (matched_terms, ratio,
    role_match, has_criteria). ratio is None when the ICP defines no keyword/industry
    criteria (so we fall back to a generic fit heuristic)."""
    icp = icp or {}
    terms = [str(t).lower().strip() for t in
             (list(icp.get("industries") or []) + list(icp.get("keywords") or []))
             if str(t).strip()]
    roles = [str(r).lower().strip() for r in (icp.get("roles") or []) if str(r).strip()]
    matched = sorted({t for t in terms if t in blob})
    ratio = (len(matched) / len(terms)) if terms else None
    role_match = bool(roles and role and any(r in role.lower() for r in roles))
    return matched, ratio, role_match, bool(terms or roles)


def _buying_intent(blob: str) -> list:
    return sorted({k for k in INTENT_KEYWORDS if k in blob})


def _disqualifiers(blob: str, icp: dict) -> list:
    found = [f"signal: {k}" for k in DISQUALIFIER_KEYWORDS if k in blob]
    for term in ((icp or {}).get("exclude") or []):
        t = str(term).lower().strip()
        if t and t in blob:
            found.append(f"excluded by ICP: {term}")
    return sorted(set(found))


def _generic_fit(data: dict) -> int:
    """Fit when no ICP keyword criteria are given: is this a real, defined,
    reachable company operating in a clear market? 0.._FIT_MAX."""
    pts = 0
    if data.get("what_they_do"):
        pts += 10
    if data.get("target_customer") or data.get("industries_served"):
        pts += 12
    if data.get("notable_customers") or data.get("metrics_or_traction"):
        pts += 10
    if data.get("competitive_positioning") or data.get("business_model"):
        pts += 8
    return min(_FIT_MAX, pts)


def _fit_level(fit_component: int, judged: bool) -> str:
    # "unknown" is reserved for the case where we couldn't assess fit at all
    # (not judged). Once judged, even a zero-match is a (weak) verdict.
    if not judged:
        return FIT_UNKNOWN
    if fit_component >= 28:
        return FIT_STRONG
    if fit_component >= 16:
        return FIT_MODERATE
    return FIT_WEAK


def _missing(data: dict, intent: list) -> list:
    checks = [
        (data.get("industries_served") or data.get("target_customer"),
         "the company's market / who they serve"),
        (intent, "any recent buying signals (hiring, funding, launches)"),
        (data.get("primary_contact_name") or data.get("founder_name"),
         "a decision-maker / economic buyer"),
        (data.get("notable_customers") or data.get("metrics_or_traction"),
         "size or traction indicators"),
    ]
    return [label for value, label in checks if not value]


def _strongest_signals(matched_icp, intent, data, role_match) -> list:
    out = []
    out += [f"ICP match: {t}" for t in matched_icp[:2]]
    out += [f"Buying signal: {s}" for s in intent[:2]]
    if data.get("notable_customers"):
        out.append(f"Notable customers: {data['notable_customers']}")
    if data.get("metrics_or_traction"):
        out.append(f"Traction: {data['metrics_or_traction']}")
    if role_match:
        out.append("Decision-maker matches target role")
    elif data.get("primary_contact_name"):
        contact = data.get("primary_contact_name")
        role = data.get("primary_contact_role")
        out.append(f"Reachable contact: {contact}" + (f" ({role})" if role else ""))
    return out[:5]


_NBA = {
    REJECT: "Pass on this lead — the fit isn't there. Put the effort into a "
            "better-matched account.",
    RESEARCH_MORE: "Gather deeper research before deciding — there isn't enough yet "
                   "to judge whether this is a fit.",
    CONTINUE: "Worth pursuing — move it into outreach when you're ready.",
    HIGH_PRIORITY: "Prioritise this one now — strong fit with active buying signals.",
}


def _confidence(recommendation, research_ok, research_score, n_missing, disq) -> int:
    if disq:
        return 85                       # a hard disqualifier is a confident skip
    if recommendation == RESEARCH_MORE:
        return min(40, (research_score if research_ok else 15))
    base = (research_score if research_ok else 30) - 5 * n_missing
    return max(20, min(95, base + 10))


# ── The decision ───────────────────────────────────────────────────────
def qualify(research=None, intel=None, icp=None) -> QualificationResult:
    """Qualify a lead from existing research. Deterministic; never fabricates."""
    research_ok = _ok(research)
    research_skip = isinstance(research, dict) and research.get("status") == "skip"
    intel_ok = _ok(intel)
    icp = icp or {}
    data = (research.get("data") if research_ok else {}) or {}
    research_score = int(research.get("research_score") or 0) if research_ok else 0

    blob = _blob(data, intel)
    role = data.get("primary_contact_role") or ""
    matched_icp, icp_ratio, role_match, has_icp = _icp_match(blob, role, icp)
    intent = _buying_intent(blob)
    disq = _disqualifiers(blob, icp)
    contact = bool(data.get("primary_contact_name") or data.get("founder_name"))

    # Score components (deterministic).
    if icp_ratio is not None:
        fit_component = round(icp_ratio * _FIT_MAX) + (5 if role_match else 0)
        fit_component = min(_FIT_MAX, fit_component)
    else:
        fit_component = _generic_fit(data)
    intent_component = min(_INTENT_MAX, 15 * len(intent))
    quality_component = round(research_score / 100 * _QUALITY_MAX) if research_ok else 0
    reach_component = _REACH_MAX if contact else 0
    score = min(100, fit_component + intent_component + quality_component + reach_component)

    usable = research_ok or intel_ok
    judged = research_ok and research_score >= WEAK_RESEARCH_SCORE
    missing = _missing(data, intent)
    strongest = _strongest_signals(matched_icp, intent, data, role_match)
    base_signals = {
        "source": "research" if research_ok else ("intel" if intel_ok else "none"),
        "research_score": research_score if research_ok else None,
        "fit_component": fit_component, "intent_component": intent_component,
        "quality_component": quality_component, "reach_component": reach_component,
        "icp_match_ratio": round(icp_ratio, 2) if icp_ratio is not None else None,
        "has_icp": has_icp, "intent_signals": len(intent),
        "has_contact": contact, "has_disqualifier": bool(disq),
    }

    def result(rec, fit_component_override=None):
        fc = fit_component if fit_component_override is None else fit_component_override
        if rec == REJECT and disq:
            prio = PRIORITY_NONE
        elif rec == HIGH_PRIORITY:
            prio = PRIORITY_HIGH
        elif rec == CONTINUE:
            prio = PRIORITY_MEDIUM
        elif rec == REJECT:
            prio = PRIORITY_LOW if score > 0 else PRIORITY_NONE
        else:  # research_more
            prio = PRIORITY_LOW if score > 0 else PRIORITY_NONE
        conf = _confidence(rec, research_ok, research_score, len(missing), disq)
        # Fit is only meaningful once we actually judged it; research_more means we
        # didn't have enough to assess fit -> report it as unknown.
        fit_level = _fit_level(fc, judged=(rec != RESEARCH_MORE))
        return QualificationResult(
            qualification_score=score,
            fit_level=fit_level,
            priority=prio, recommendation=rec, confidence=conf,
            strongest_signals=strongest, disqualifiers=disq,
            missing_information=missing, next_best_action=_NBA[rec],
            reasoning_summary=_summary(rec, score, fit_component, intent, disq),
            signals=base_signals)

    # ---- Ladder (order matters) ----
    # 1. A hard disqualifier is decisive — skip regardless of anything else.
    if disq:
        return result(REJECT)
    # 2. Nothing usable, or research too thin to judge fit -> gather more first.
    if not usable or (not research_ok and not intel_ok):
        return result(RESEARCH_MORE)
    if research_skip or (research_ok and research_score < WEAK_RESEARCH_SCORE) or not judged:
        # intel-only or weak/skip research: not enough to responsibly judge fit
        return result(RESEARCH_MORE)
    # 3. Strong fit + active buying intent -> pursue now.
    if score >= QUALIFY_HIGH and intent:
        return result(HIGH_PRIORITY)
    # 4. Reasonable fit -> pursue normally.
    if score >= QUALIFY_CONTINUE:
        return result(CONTINUE)
    # 5. Adequate research but genuinely poor fit -> reject (a fit problem, not a
    #    research problem).
    return result(REJECT)


def _summary(rec, score, fit, intent, disq) -> str:
    if disq:
        return f"Disqualified ({'; '.join(disq)}) — not a viable lead."
    head = f"Qualification {score}/100 (fit {fit}/{_FIT_MAX}"
    head += f", {len(intent)} buying signal(s))." if intent else ", no buying signals)."
    tail = {
        HIGH_PRIORITY: "Strong fit with active intent — worth pursuing now.",
        CONTINUE: "A reasonable fit — worth pursuing.",
        RESEARCH_MORE: "Too little verified detail to judge fit — research deeper first.",
        REJECT: "Fit is too weak to justify outreach.",
    }[rec]
    return f"{head} {tail}"


def qualify_from_workspace(workspace: dict) -> QualificationResult:
    """Convenience: pull research/intel/icp from a chat thread's workspace and
    qualify. Keeps the core ``qualify`` decoupled from the workspace shape."""
    ws = workspace or {}
    return qualify(research=ws.get("research"), intel=ws.get("intel"),
                   icp=ws.get("icp"))
