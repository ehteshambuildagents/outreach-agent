"""Apollo People-Match provider — verified person-level contact enrichment.

Given identifiers the research pipeline already has (a contact NAME, the company
DOMAIN, optionally a LinkedIn URL), Apollo's People Match endpoint returns that
person's verified professional email, exact title/seniority, and canonical
LinkedIn URL. This is the person-level enrichment step the pipeline lacked: the
crawler finds WHO to contact plus a generic scraped address (info@/hello@), and
Apollo upgrades that to the individual's real work email when it is higher
confidence. (This is what the never-finished Hunter.io slot was reserved for.)

Cost-controlled + safe, exactly like the other paid providers:
  * key read from APOLLO_API_KEY (never hard-coded, never logged);
  * every call routes through providers_common.request_json, so it inherits the
    per-user spend caps/metering (provider "apollo"), transient-only retries, and
    graceful None-on-failure — it NEVER raises;
  * no match / missing key / failure all return a typed status dict, so the
    pipeline continues unchanged (a null person is a normal, expected outcome).

Enrichment is OFF the always-on research path: it costs money per match, so a
caller must gate it behind APOLLO_ENRICH_ENABLED (see config.settings) and only
run it when a usable identifier is available. This module owns the API call and
the mapping; wiring it into the live pipeline is a separate, deliberate step.
"""

import logging

from research.providers_common import get_key, request_json

log = logging.getLogger("research.apollo")

_ENDPOINT = "https://api.apollo.io/api/v1/people/match"
_ENV = "APOLLO_API_KEY"
_PROVIDER = "apollo"

# Apollo email_status we treat as high-confidence — a verified individual address
# always beats a generic scraped one.
_HIGH_CONFIDENCE_EMAIL = {"verified"}
# Generic/role mailboxes the scraper often finds; Apollo's person-specific email
# should win over these even when Apollo's own status is only "likely"/unknown.
_GENERIC_LOCALPARTS = {
    "info", "hello", "hi", "hey", "contact", "support", "sales", "team", "admin",
    "help", "office", "press", "media", "hq", "careers", "jobs", "enquiries",
    "inquiries", "no-reply", "noreply", "mail", "general",
}
# Vague roles worth replacing with Apollo's specific title; a real role the
# research already found is left untouched (conservative — no clobbering).
_GENERIC_ROLES = {"team member", "employee", "staff", "contact", "member", "team"}


def available() -> bool:
    """True only when APOLLO_API_KEY is configured (else enrichment is skipped)."""
    return bool(get_key(_ENV))


def is_generic_email(email: str) -> bool:
    """True when an address is a generic/role mailbox (info@, hello@, sales@, …)
    rather than a specific individual's. A caller uses this to decide whether a
    paid Apollo lookup is even worth making (we already have a person's address ->
    skip)."""
    return _is_generic(email)


def enrich_person(*, name=None, first_name=None, last_name=None, domain=None,
                  organization_name=None, linkedin_url=None,
                  reveal_personal_emails=False) -> dict:
    """One Apollo People-Match call. NEVER raises. Returns:

        {"status": "ok"|"no_match"|"unavailable"|"error",
         "person": {...}|None, "reason"?: str}

    Send whatever identifiers we have; Apollo matches on them. On success,
    ``person`` is the minimized subset we actually use downstream (see _person()).
    A missing key, a capped user, a transient failure, a 4xx, or a genuine
    no-match all resolve to a status dict — the caller is never surprised by an
    exception and the surrounding pipeline is unaffected.
    """
    if not available():
        return _result("unavailable",
                       reason="Apollo isn't configured (set APOLLO_API_KEY).")

    body = _body(name=name, first_name=first_name, last_name=last_name,
                 domain=domain, organization_name=organization_name,
                 linkedin_url=linkedin_url,
                 reveal_personal_emails=reveal_personal_emails)
    # Need at least one identifying signal, or the (paid) call is wasted.
    if not any(body.get(k) for k in
               ("name", "first_name", "last_name", "linkedin_url")):
        return _result("error",
                       reason="No usable identifier (need a name or LinkedIn URL).")

    data = request_json("POST", _ENDPOINT, provider=_PROVIDER,
                        headers=_headers(), json_body=body)
    if data is None:
        # request_json already logged the reason; cap/transient/4xx all land here.
        return _result("error", reason="Apollo request failed (see logs).")

    person = data.get("person")
    if not person:
        log.info("apollo: no match (%s)", body.get("name") or body.get("domain"))
        return _result("no_match", reason="Apollo found no matching person.")

    return _result("ok", person=_person(person))


def merge_into_research(data: dict, person: dict) -> dict:
    """Map an Apollo ``person`` (from ``enrich_person(...)['person']``) into the
    research ``data`` dict, upgrading contact fields ONLY when Apollo is higher
    confidence than the scraped value. Mutates and returns ``data``.

    Pure, no network — unit-tested. NOT called by the live pipeline yet; the
    enrichment call site stays gated until it is deliberately turned on.
    """
    if not person:
        return data

    email = (person.get("email") or "").strip()
    status = (person.get("email_status") or "").strip().lower()
    if email and _email_upgrades(email, status, data.get("public_contact_email")):
        data["primary_contact_email"] = email
        # A verified individual address becomes the actual send route.
        data["recipient_route"] = email
        if not data.get("public_contact_email"):
            data["public_contact_email"] = email

    title = (person.get("title") or "").strip()
    if title and _role_upgrades(title, data.get("primary_contact_role")):
        data["primary_contact_role"] = title

    if not data.get("primary_contact_name") and person.get("name"):
        data["primary_contact_name"] = person["name"]

    if not data.get("linkedin_url") and person.get("linkedin_url"):
        data["linkedin_url"] = person["linkedin_url"]

    # Provenance so downstream/telemetry can see this came from Apollo.
    data["contact_enrichment"] = {
        "source": "apollo",
        "email_status": status or None,
        "seniority": person.get("seniority"),
    }
    return data


# ── Helpers ────────────────────────────────────────────────────────────────
def _result(status, *, person=None, reason=None) -> dict:
    out = {"status": status, "person": person}
    if reason:
        out["reason"] = reason
    return out


def _headers() -> dict:
    # Apollo's REST API authenticates with the X-Api-Key header. The key is read
    # per call from the environment and is never logged.
    return {
        "X-Api-Key": get_key(_ENV),
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
    }


def _body(*, name, first_name, last_name, domain, organization_name,
          linkedin_url, reveal_personal_emails) -> dict:
    """Build the request body from whatever identifiers we have, dropping empties
    so we never send blank fields."""
    raw = {
        "name": _clean(name),
        "first_name": _clean(first_name),
        "last_name": _clean(last_name),
        "domain": _domain(domain),
        "organization_name": _clean(organization_name),
        "linkedin_url": _clean(linkedin_url),
    }
    body = {k: v for k, v in raw.items() if v}
    # Personal emails cost extra credits + carry privacy weight; opt-in only.
    body["reveal_personal_emails"] = bool(reveal_personal_emails)
    # Phone reveal requires a webhook_url and extra credits; never requested here.
    body["reveal_phone_number"] = False
    return body


def _person(person: dict) -> dict:
    """Minimize Apollo's large person object to the fields we actually use."""
    org = person.get("organization") or {}
    return {
        "id": person.get("id"),
        "name": person.get("name"),
        "first_name": person.get("first_name"),
        "last_name": person.get("last_name"),
        "title": person.get("title"),
        "seniority": person.get("seniority"),
        "email": person.get("email"),
        "email_status": person.get("email_status"),
        "linkedin_url": person.get("linkedin_url"),
        "organization_name": org.get("name"),
        "organization_domain": org.get("primary_domain") or org.get("website_url"),
    }


def _email_upgrades(new_email: str, new_status: str, current: str) -> bool:
    """True when Apollo's email should replace the scraped one: there is no
    current email, the current one is a generic/role mailbox, or Apollo's is
    verified."""
    if not current:
        return True
    if new_status in _HIGH_CONFIDENCE_EMAIL:
        return True
    return _is_generic(current)


def _role_upgrades(new_role: str, current: str) -> bool:
    """Fill a missing role, or replace a vague placeholder with Apollo's specific
    title; never clobber a real role the research already established."""
    if not current:
        return True
    return current.strip().lower() in _GENERIC_ROLES


def _is_generic(email: str) -> bool:
    local = (email or "").split("@", 1)[0].strip().lower()
    return local in _GENERIC_LOCALPARTS


def _clean(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _domain(value):
    """Normalize a domain: strip scheme/path/www ('https://www.acme.com/x' ->
    'acme.com') so Apollo gets a clean employer domain."""
    text = _clean(value)
    if not text:
        return None
    text = text.lower()
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    text = text.split("/", 1)[0].strip()
    if text.startswith("www."):
        text = text[4:]
    return text or None
