"""
Credentials must not reach a contract file.

Contracts land in ``contracts/``, which is repo-relative and not gitignored, so
anything written there is one ``git add`` from being published. Request headers
were already scrubbed against a three-name denylist; the query string, parsed
three lines away, was stored verbatim.

The tests below are in two halves, and the second half is the one that keeps
this honest. Redaction is trivially "passed" by destroying everything — a
function returning ``{}`` satisfies every leak test on this page. The controls
assert that ordinary headers, ordinary parameters and the parameter *names*
all survive, so a redactor that eats the document fails too.
"""

from __future__ import annotations

import json

import pytest

from contract_testing.consumer import ConsumerContractGenerator
from contract_testing.redaction import (
    REDACTED,
    is_sensitive_name,
    redact_headers,
    redact_query,
)

SECRET = "live_sk_9f3c2a77b41e"


class TestNamesTheOldDenylistMissed:
    """Each of these reached disk under the three-name denylist."""

    @pytest.mark.parametrize(
        "name",
        [
            "x-auth-token",
            "api-key",
            "apikey",
            "api_key",
            "authentication",
            "proxy-authorization",
            "x-amz-security-token",
            "x-session-id",
            "x-csrf-token",
            "access_token",
            "refresh_token",
            "client_secret",
            "password",
            "x-signature",
            "sig",
            "otp",
            # the three the old list did cover, which must still be covered
            "authorization",
            "cookie",
            "x-api-key",
        ],
    )
    def test_is_recognised_as_sensitive(self, name):
        assert is_sensitive_name(name)

    @pytest.mark.parametrize("name", ["AUTHORIZATION", "X-Api-Key", "Api_Key"])
    def test_matching_ignores_case(self, name):
        assert is_sensitive_name(name)


class TestOrdinaryNamesSurvive:
    """
    Positive control. A redactor that flagged everything would pass every test
    in the class above while making contracts useless.
    """

    @pytest.mark.parametrize(
        "name",
        [
            "content-type",
            "accept",
            "accept-language",
            "x-request-id",
            "if-none-match",
            "page",
            "limit",
            "sort",
            "status",
            "monkey",  # contains 'key' as a substring, must NOT match
            "keyboard_layout",
            "signal",  # contains 'sig' as a substring, must NOT match
        ],
    )
    def test_is_not_treated_as_sensitive(self, name):
        assert not is_sensitive_name(name)


class TestHeaderRedaction:
    def test_the_secret_value_is_gone(self):
        out = redact_headers({"Authorization": f"Bearer {SECRET}"})
        assert SECRET not in json.dumps(out)

    def test_the_header_name_is_kept(self):
        """
        The contract records that this endpoint needs auth. Dropping the header
        — what the old code did — loses that, and the provider side validates
        responses only, so keeping it costs nothing.
        """
        assert "Authorization" in redact_headers({"Authorization": f"Bearer {SECRET}"})

    def test_ordinary_headers_pass_through_untouched(self):
        headers = {"Content-Type": "application/json", "X-Request-Id": "abc-123"}
        assert redact_headers(headers) == headers

    def test_a_mixed_set_keeps_the_harmless_half(self):
        out = redact_headers({"Content-Type": "application/json", "X-Api-Key": SECRET})
        assert out == {"Content-Type": "application/json", "X-Api-Key": REDACTED}


class TestQueryRedaction:
    def test_a_token_parameter_is_redacted(self):
        assert SECRET not in redact_query(f"api_key={SECRET}")

    def test_the_parameter_name_survives(self):
        assert redact_query(f"api_key={SECRET}") == f"api_key={REDACTED}"

    def test_ordinary_parameters_are_untouched(self):
        assert redact_query("page=2&limit=50") == "page=2&limit=50"

    def test_only_the_sensitive_parameter_is_hit(self):
        out = redact_query(f"page=2&token={SECRET}&limit=50")
        assert "page=2" in out
        assert "limit=50" in out
        assert SECRET not in out

    def test_a_presigned_url_signature_is_redacted(self):
        query = f"X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature={SECRET}"
        out = redact_query(query)
        assert SECRET not in out
        assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in out

    def test_an_empty_query_stays_empty(self):
        assert redact_query("") == ""

    def test_a_query_that_is_not_key_value_is_redacted_whole(self):
        """A bare signed blob — we cannot tell whether it holds a token."""
        assert redact_query("eyJhbGciOiJIUzI1NiJ9") == REDACTED

    def test_blank_values_keep_their_parameter(self):
        assert redact_query("debug=") == "debug="


class TestNothingLeaksIntoAGeneratedContract:
    """End to end: the serialised JSON is what actually reaches the repo."""

    def _contract_json(self, url_query: str, headers: dict) -> str:
        raw = {
            "method": "GET",
            "path": "/reports",
            "query": url_query,
            "request_headers": headers,
            "request_body": None,
            "status": 200,
            "response_headers": {},
            "response_body": {"ok": True},
            "test_name": "tests/test_x.py::test_y",
        }
        generator = ConsumerContractGenerator(consumer="wiki_project", provider="api")
        return json.dumps(generator.from_captures([raw]).to_dict())

    def test_a_query_token_does_not_reach_the_json(self):
        assert SECRET not in self._contract_json(f"api_key={SECRET}", {})

    def test_an_unlisted_auth_header_does_not_reach_the_json(self):
        assert SECRET not in self._contract_json("", {"X-Auth-Token": SECRET})

    def test_the_contract_still_describes_the_request(self):
        """
        Control for the two above: they pass trivially if the generator emits
        nothing at all.
        """
        body = self._contract_json("page=2", {"Content-Type": "application/json"})
        assert "/reports" in body
        assert "page=2" in body
        assert "Content-Type" in body
