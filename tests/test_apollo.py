"""Apollo People-Match provider — offline unit tests.

These MOCK the Apollo HTTP layer (research.apollo.request_json) so the suite never
hits the real API and never costs credits. The single real, live round-trip is a
separate, deliberate verification (a scratch script), not part of this suite.

Coverage: the enrich_person status contract (ok / no_match / unavailable / error /
no-identifier), request-body construction, and — the part the privacy-safe live
call could not show — merge_into_research upgrading contact fields ONLY when
Apollo is higher confidence than the scraped value.
"""

import unittest
from unittest import mock

import research.apollo as apollo


# A fully-populated Apollo person (what a real match looks like), plus a junk
# field to prove _person() minimizes down to what we actually use.
_FULL_PERSON = {
    "id": "x1", "name": "Dana Lee", "first_name": "Dana", "last_name": "Lee",
    "title": "VP of Growth", "seniority": "vp",
    "email": "dana@acme.com", "email_status": "verified",
    "linkedin_url": "https://linkedin.com/in/danalee",
    "organization": {"name": "Acme", "primary_domain": "acme.com"},
    "photo_url": "…", "employment_history": [{"…": "…"}],  # ignored by _person()
}


def _has_key(*_a, **_k):
    return "test-key"


class EnrichPersonContractTests(unittest.TestCase):
    @mock.patch("research.apollo.get_key", _has_key)
    @mock.patch("research.apollo.request_json")
    def test_ok_returns_minimized_person_and_posts_identifiers(self, m):
        m.return_value = {"person": _FULL_PERSON}
        out = apollo.enrich_person(first_name="Dana", last_name="Lee",
                                   domain="https://www.acme.com/team")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["person"]["email"], "dana@acme.com")
        self.assertEqual(out["person"]["email_status"], "verified")
        self.assertEqual(out["person"]["organization_domain"], "acme.com")
        self.assertNotIn("photo_url", out["person"])          # minimized
        # It POSTed to the match endpoint, metered as "apollo", with clean body.
        args, kwargs = m.call_args
        self.assertEqual(args[0], "POST")
        self.assertIn("people/match", args[1])
        self.assertEqual(kwargs["provider"], "apollo")
        self.assertEqual(kwargs["json_body"]["first_name"], "Dana")
        self.assertEqual(kwargs["json_body"]["domain"], "acme.com")  # normalized

    @mock.patch("research.apollo.get_key", _has_key)
    @mock.patch("research.apollo.request_json", return_value={"person": None})
    def test_no_match(self, _m):
        out = apollo.enrich_person(name="Nobody Here", domain="acme.com")
        self.assertEqual(out["status"], "no_match")
        self.assertIsNone(out["person"])

    @mock.patch("research.apollo.get_key", _has_key)
    @mock.patch("research.apollo.request_json", return_value=None)
    def test_request_failure_is_error(self, _m):
        out = apollo.enrich_person(name="Dana Lee", domain="acme.com")
        self.assertEqual(out["status"], "error")

    @mock.patch("research.apollo.get_key", return_value="")     # no key configured
    @mock.patch("research.apollo.request_json")
    def test_unavailable_without_key_never_calls_api(self, m, _gk):
        out = apollo.enrich_person(name="Dana Lee", domain="acme.com")
        self.assertEqual(out["status"], "unavailable")
        m.assert_not_called()

    @mock.patch("research.apollo.get_key", _has_key)
    @mock.patch("research.apollo.request_json")
    def test_no_identifier_is_error_and_never_calls_api(self, m):
        # Only a domain/org — no name or LinkedIn — is not enough to match a person.
        out = apollo.enrich_person(domain="acme.com", organization_name="Acme")
        self.assertEqual(out["status"], "error")
        m.assert_not_called()

    @mock.patch("research.apollo.get_key", _has_key)
    @mock.patch("research.apollo.request_json")
    def test_person_with_null_email_stays_ok_but_empty(self, m):
        # Mirrors the real placeholder-name live response: a person record exists
        # but carries no contact data. Must NOT be mistaken for real detail.
        m.return_value = {"person": {"name": "Pipeline Smoketest",
                                     "organization": {"name": "Apollo.io",
                                                      "primary_domain": "apollo.io"}}}
        out = apollo.enrich_person(first_name="Pipeline", last_name="Smoketest",
                                   domain="apollo.io")
        self.assertEqual(out["status"], "ok")
        self.assertIsNone(out["person"]["email"])


class BodyTests(unittest.TestCase):
    def test_drops_empties_normalizes_domain_and_sets_reveal_flags(self):
        body = apollo._body(name=None, first_name="Dana", last_name=None,
                            domain="HTTP://www.Acme.com/team",
                            organization_name="  Acme  ", linkedin_url=None,
                            reveal_personal_emails=False)
        self.assertEqual(body["domain"], "acme.com")
        self.assertEqual(body["organization_name"], "Acme")
        self.assertEqual(body["first_name"], "Dana")
        self.assertNotIn("name", body)          # None dropped
        self.assertNotIn("last_name", body)     # None dropped
        self.assertNotIn("linkedin_url", body)  # None dropped
        self.assertIs(body["reveal_personal_emails"], False)
        self.assertIs(body["reveal_phone_number"], False)


class MergeTests(unittest.TestCase):
    def _person(self, **over):
        p = {"name": "Dana Lee", "title": "VP of Growth", "seniority": "vp",
             "email": "dana@acme.com", "email_status": "verified",
             "linkedin_url": "https://linkedin.com/in/danalee"}
        p.update(over)
        return p

    def test_verified_email_upgrades_generic_scraped(self):
        data = {"public_contact_email": "info@acme.com", "primary_contact_role": ""}
        apollo.merge_into_research(data, self._person())
        self.assertEqual(data["primary_contact_email"], "dana@acme.com")
        self.assertEqual(data["recipient_route"], "dana@acme.com")
        self.assertEqual(data["primary_contact_role"], "VP of Growth")
        self.assertEqual(data["contact_enrichment"]["source"], "apollo")
        self.assertEqual(data["contact_enrichment"]["email_status"], "verified")

    def test_fills_when_no_existing_email(self):
        data = {}
        apollo.merge_into_research(data, self._person())
        self.assertEqual(data["primary_contact_email"], "dana@acme.com")
        self.assertEqual(data["public_contact_email"], "dana@acme.com")

    def test_unverified_does_not_clobber_a_specific_existing_email(self):
        data = {"public_contact_email": "founder@acme.com"}  # specific, non-generic
        apollo.merge_into_research(
            data, self._person(email="d.lee@acme.com", email_status="likely"))
        self.assertNotIn("primary_contact_email", data)
        self.assertEqual(data["public_contact_email"], "founder@acme.com")

    def test_verified_overrides_even_a_specific_existing_email(self):
        data = {"public_contact_email": "old@acme.com"}
        apollo.merge_into_research(
            data, self._person(email="dana@acme.com", email_status="verified"))
        self.assertEqual(data["primary_contact_email"], "dana@acme.com")

    def test_null_email_person_sets_no_email(self):
        data = {"public_contact_email": "info@acme.com"}
        apollo.merge_into_research(data, {"name": "X", "email": None})
        self.assertNotIn("primary_contact_email", data)
        self.assertEqual(data["public_contact_email"], "info@acme.com")

    def test_does_not_clobber_a_real_role(self):
        data = {"primary_contact_role": "Chief Executive Officer"}
        apollo.merge_into_research(data, self._person(title="VP of Growth"))
        self.assertEqual(data["primary_contact_role"], "Chief Executive Officer")

    def test_fills_missing_name_and_linkedin(self):
        data = {}
        apollo.merge_into_research(data, self._person())
        self.assertEqual(data["primary_contact_name"], "Dana Lee")
        self.assertEqual(data["linkedin_url"], "https://linkedin.com/in/danalee")

    def test_empty_person_is_a_noop(self):
        data = {"public_contact_email": "info@acme.com"}
        apollo.merge_into_research(data, None)
        self.assertEqual(data, {"public_contact_email": "info@acme.com"})


class AvailableTests(unittest.TestCase):
    @mock.patch("research.apollo.get_key", return_value="k")
    def test_available_true_with_key(self, _):
        self.assertTrue(apollo.available())

    @mock.patch("research.apollo.get_key", return_value="")
    def test_available_false_without_key(self, _):
        self.assertFalse(apollo.available())


if __name__ == "__main__":
    unittest.main()
