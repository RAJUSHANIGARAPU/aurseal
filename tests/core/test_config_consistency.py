"""Every endpoint an environment names lives in that environment's host family."""

from __future__ import annotations

import json

import pytest

from core.config_consistency import (
    check_environments,
    host_family,
    host_of,
    is_endpoint_key,
    same_host_family,
)
from core.config_reader import _ENVIRONMENTS_FILE


class TestHostFamily:
    @pytest.mark.parametrize(
        ("value", "family"),
        [
            ("https://www.shop.example.test/en/", "example.test"),
            ("https://api.example.test", "example.test"),
            ("docs.example.test:8443", "example.test"),
            ("https://a.b.example.co.uk/", "example.co.uk"),
            ("http://localhost:8800/", "localhost"),
            ("http://127.0.0.1:9000", "localhost"),
            ("http://[::1]:9000", "localhost"),
            ("http://app.localhost/", "localhost"),
            ("http://10.0.0.5/", "10.0.0.5"),
            ("", ""),
        ],
    )
    def test_family(self, value, family):
        assert host_family(value) == family

    def test_aliases_join_two_domains(self):
        aliases = {"example-cdn.test": "example.test"}
        assert same_host_family("https://a.example.test", "https://x.example-cdn.test", aliases)
        assert not same_host_family("https://a.example.test", "https://x.example-cdn.test")

    def test_empty_is_never_the_same_family(self):
        assert not same_host_family("", "")

    def test_bare_host_is_parsed(self):
        assert host_of("Docs.Example.test:80") == "docs.example.test"

    def test_endpoint_keys(self):
        assert is_endpoint_key("api_base_url")
        assert is_endpoint_key("identity_host")
        assert not is_endpoint_key("browser")


class TestCheckEnvironments:
    def test_consistent_envs_pass(self):
        envs = {
            "qa": {
                "base_url": "https://qa.example.test/",
                "api_url": "https://api-qa.example.test",
            },
            "local": {"base_url": "http://localhost:8800/", "api_url": "http://127.0.0.1:8800"},
        }
        assert check_environments(envs) == []

    def test_a_host_from_another_family_is_reported(self):
        envs = {
            "qa": {"base_url": "https://qa.example.test/", "api_url": "https://api.other.test"},
        }
        problems = check_environments(envs)
        assert problems == [
            "qa: api_url host 'api.other.test' is not in the family of base_url host "
            "'qa.example.test'"
        ]

    def test_a_missing_key_fails_rather_than_falling_back(self):
        envs = {
            "qa": {"base_url": "https://qa.example.test/", "docs_host": "docs.example.test"},
            "staging": {"base_url": "https://staging.example.test/"},
        }
        assert check_environments(envs) == [
            "staging: docs_host is missing (it must not fall back to another env)"
        ]

    def test_a_blank_key_counts_as_missing(self):
        envs = {"qa": {"base_url": "https://qa.example.test/", "api_url": "  "}}
        assert "missing" in check_environments(envs)[0]

    def test_required_keys_are_enforced_even_when_nobody_defines_them(self):
        envs = {"qa": {"base_url": "https://qa.example.test/"}}
        assert check_environments(envs, required=["identity_host"]) == [
            "qa: identity_host is missing (it must not fall back to another env)"
        ]

    def test_missing_base_url_is_reported(self):
        assert check_environments({"qa": {"api_url": "https://x.example.test"}}) == [
            "qa: base_url is missing or has no host"
        ]

    def test_the_committed_environments_are_consistent(self):
        environments = json.loads(_ENVIRONMENTS_FILE.read_text(encoding="utf-8"))
        assert check_environments(environments, required=["api_base_url"]) == []
