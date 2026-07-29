"""Customer-likelihood scoring: how likely is this company to BUY, not how well
does it match the words.

Those are different problems, and optimising the second is what produced a page of
perfectly-on-topic companies with no reason to be in-market. The score is an
explicit weighted sum over five factors, and the weights encode one rule the
product cares about more than tidy relevance:

    A slightly less relevant company with a strong buying signal outranks a
    perfectly matching company with no indication it is in-market.

That holds arithmetically, not by intention: buying signals plus hiring relevance
carry 0.50 of the score while ICP match carries 0.30, so a 0.6-relevance company
with both signals (0.68) beats a 1.0-relevance company with neither (0.30). There
is a test that asserts exactly this.

Every factor returns a 0..1 subscore AND the human-readable reasons behind it, so
"why is this ranked here" is always answerable from the data.
"""

from dataclasses import dataclass, field
from typing import List

# The weights. They sum to 1.0, and the ordering is the product decision:
# in-market beats on-topic.
W_ICP = 0.30
W_BUYING = 0.25
W_HIRING = 0.25
W_ACCESS = 0.10
W_CORROBORATION = 0.10


@dataclass
class Score:
    total: float = 0.0
    icp: float = 0.0
    buying: float = 0.0
    hiring: float = 0.0
    access: float = 0.0
    corroboration: float = 0.0
    reasons: List[str] = field(default_factory=list)

    def public(self) -> dict:
        return {
            "total": round(self.total, 3),
            "icp_match": round(self.icp, 2),
            "buying_signal": round(self.buying, 2),
            "hiring_relevance": round(self.hiring, 2),
            "founder_access": round(self.access, 2),
            "corroboration": round(self.corroboration, 2),
        }


def score_prospect(prospect, plan) -> Score:
    """Score one candidate against the plan. Pure: no network, no model."""
    s = Score()
    s.icp, icp_reasons = _icp(prospect, plan)
    s.buying, buy_reasons = _buying(prospect)
    s.hiring, hire_reasons = _hiring(prospect, plan)
    s.access, access_reasons = _access(prospect, plan)
    s.corroboration, corr_reasons = _corroboration(prospect)
    s.total = (W_ICP * s.icp + W_BUYING * s.buying + W_HIRING * s.hiring
               + W_ACCESS * s.access + W_CORROBORATION * s.corroboration)
    # A demoted intermediary can never win on signal strength alone.
    if getattr(prospect, "tier", "company") != "company":
        s.total *= 0.35
        s.reasons.append("Fallback source, ranked below real companies")
    # Enterprise-scale companies are demoted AS CUSTOMERS. A role-title search on
    # Apollo returns Amazon, Microsoft and Disney at the top simply because they
    # post the most jobs, and none of them is a plausible buyer for a founder's AI
    # SDR. This is a demotion, not a drop, so they still appear if nothing better
    # exists; it is stronger when the ask is explicitly about founder-led companies.
    if _is_enterprise(prospect):
        factor = 0.40 if getattr(plan, "wants_founder_access", False) else 0.55
        s.total *= factor
        s.reasons.append("Large/enterprise company — unlikely to buy from a "
                         "founder-led tool; ranked below reachable companies")
    s.reasons.extend(hire_reasons + buy_reasons + icp_reasons + access_reasons
                     + corr_reasons)
    return s


def _is_enterprise(p) -> bool:
    """A company a founder cannot realistically close: publicly traded, or with
    nine-figure revenue / four-figure headcount. Deterministic off Apollo's data."""
    if getattr(p, "is_public", False):
        return True
    revenue = getattr(p, "annual_revenue", None)
    if isinstance(revenue, (int, float)) and revenue >= 500_000_000:
        return True
    employees = getattr(p, "employee_count", None)
    if isinstance(employees, int) and employees >= 1000:
        return True
    return False


# ── 1. ICP match: is this the KIND of company the user sells to? ───────────
def _icp(p, plan) -> tuple:
    reasons, score = [], 0.35          # unknown is not zero: absence != mismatch
    industry_kind = getattr(p, "industry_kind", "") or ""
    if industry_kind == "software":
        score += 0.35
        reasons.append("Software product company, not a services shop")
    elif industry_kind == "services":
        score -= 0.15
        reasons.append("Looks like services or consulting rather than a product")

    haystack = " ".join(str(x or "").lower() for x in
                        [p.company_name, p.domain, p.industry,
                         " ".join(getattr(p, "basic_signals", []) or [])])
    hits = [t for t in (getattr(plan, "relevance_terms", None) or [])
            if t and t.lower() in haystack]
    if hits:
        score += min(0.20, 0.10 * len(hits))
        reasons.append("Close to what you asked about: " + ", ".join(hits))

    if getattr(plan, "company_type", "any") == "b2b" and industry_kind == "software":
        score += 0.10

    size_band = getattr(plan, "size_band", ()) or ()
    employees = getattr(p, "employee_count", None)
    if size_band and isinstance(employees, int) and employees > 0:
        if size_band[0] <= employees <= size_band[1]:
            score += 0.10
            reasons.append(f"About {employees} employees, inside your range")
        else:
            score -= 0.10
            reasons.append(f"About {employees} employees, outside your range")
    return _clamp(score), reasons


# ── 2. Buying signal: is anything happening that means money is moving? ────
def _buying(p) -> tuple:
    reasons, score = [], 0.0
    growth = ((getattr(p, "growth", None) or {}).get("headcount_6mo"))
    if isinstance(growth, (int, float)):
        if growth >= 0.20:
            score += 0.45
            reasons.append(f"Headcount up {round(growth * 100)}% in six months")
        elif growth >= 0.05:
            score += 0.25
            reasons.append(f"Headcount up {round(growth * 100)}% in six months")
        elif growth <= -0.15:
            score -= 0.15
            reasons.append(f"Headcount down {abs(round(growth * 100))}% in six months")

    hiring = getattr(p, "hiring", None) or {}
    open_roles = hiring.get("open_roles")
    if isinstance(open_roles, int) and open_roles > 0:
        # Actively spending on people at all is a real signal, separate from
        # whether the roles match what the user asked about.
        score += 0.30 if open_roles >= 5 else 0.18
        reasons.append(f"{open_roles} open roles right now")

    if getattr(p, "has_intent", False):
        score += 0.25
        reasons.append("Apollo shows buying intent on this account")

    for signal in (getattr(p, "basic_signals", None) or []):
        if signal in ("raised funding", "series a", "series b", "seed"):
            score += 0.15
            reasons.append(f"Funding signal: {signal}")
            break

    activity = getattr(p, "recent_activity", None) or {}
    if activity.get("summary"):
        score += 0.15
        reasons.append(activity["summary"])
    return _clamp(score), reasons


# ── 3. Hiring relevance: are they hiring the thing that matters? ───────────
def _hiring(p, plan) -> tuple:
    hiring = getattr(p, "hiring", None) or {}
    match = hiring.get("match")
    role_asked = bool(getattr(plan, "roles", None))
    if not hiring.get("verified"):
        # No hiring signal asked for => this factor is neutral, not a penalty.
        return (0.0 if role_asked else 0.5), []
    if match == "role":
        return 1.0, [hiring.get("summary") or "Hiring for the role you asked about"]
    # "Hiring, but not for that role" must NOT count as hiring-RELEVANCE when a
    # specific role was asked for: presenting a company hiring content creators as
    # a match for an SDR search is exactly the mislabelling to avoid. The fact that
    # they're hiring at all is still credited once, as a buying signal (see
    # _buying's open_roles). When no role was asked, any active hiring is mildly
    # relevant, so the old weights stand.
    if match == "any":
        return (0.0 if role_asked else 0.35), [
            hiring.get("summary") or "Hiring, but not for the role you asked about"]
    return (0.0 if role_asked else 0.2), [hiring.get("summary") or "Has a careers page"]


# ── 4. Founder accessibility: can this actually be sold to by one person? ──
def _access(p, plan) -> tuple:
    reasons, score = [], 0.4
    # Scale is the strongest accessibility signal available. Apollo's company
    # search does not return a headcount, so revenue and public-market status stand
    # in for it: a public or nine-figure company has a buying committee, and a cold
    # email from a founder does not reach the person who signs.
    if getattr(p, "is_public", False):
        score -= 0.45
        reasons.append("Publicly traded, so expect procurement, not a founder")
    revenue = getattr(p, "annual_revenue", None)
    if isinstance(revenue, (int, float)) and revenue > 0:
        if revenue >= 500_000_000:
            score -= 0.40
            reasons.append("Enterprise-scale revenue, hard for a founder to reach")
        elif revenue >= 50_000_000:
            score -= 0.20
            reasons.append("Mid-market revenue, a founder is harder to reach")
        elif revenue <= 10_000_000:
            score += 0.25
            reasons.append("Small company, a founder is usually reachable directly")
    employees = getattr(p, "employee_count", None)
    if isinstance(employees, int) and employees > 0:
        if employees <= 50:
            score += 0.45
            reasons.append("Small enough that a founder usually answers directly")
        elif employees <= 200:
            score += 0.20
        elif employees >= 1000:
            score -= 0.25
            reasons.append("Large company, expect procurement rather than a founder")
    founded = getattr(p, "founded_year", None)
    if isinstance(founded, int) and founded > 0:
        if founded >= 2019:
            score += 0.15
            reasons.append(f"Founded {founded}, still founder-led in most cases")
        elif founded < 2005:
            score -= 0.10          # decades-old incumbents are rarely founder-led
    if getattr(p, "contact_route", ""):
        score += 0.10
    return _clamp(score), reasons


# ── 5. Corroboration: did more than one independent source find it? ────────
def _corroboration(p) -> tuple:
    sources = {s.get("provider") for s in (getattr(p, "sources", None) or [])
               if isinstance(s, dict)}
    sources.discard(None)
    if len(sources) >= 3:
        return 1.0, ["Found independently by " + " + ".join(sorted(sources))]
    if len(sources) == 2:
        return 0.7, ["Found independently by " + " + ".join(sorted(sources))]
    return 0.3, []


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
