"""Token and storage-state cache: JWT expiry, opaque sessions, atomic writes."""

from __future__ import annotations

import base64
import json
import os

import pytest

from core.auth.session_token_store import (
    SessionTokenStore,
    b64url_decode,
    jwt_claims,
    jwt_expiry,
    jwt_is_usable,
    write_atomically,
)

NOW = 1_800_000_000.0


def _segment(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def make_jwt(claims: dict) -> str:
    return f"{_segment({'alg': 'none'})}.{_segment(claims)}.sig"


def jwt_with_url_safe_characters(exp: float) -> str:
    """
    A JWT whose payload segment contains both '-' and '_'.

    '>>>' encodes to 'Pj4-' and '???' to 'Pz8_' in the URL-safe alphabet: exactly
    the characters a standard-alphabet decoder rejects.
    """
    token = make_jwt({"exp": exp, "pad": ">>>???" * 3})
    payload = token.split(".")[1]
    assert "-" in payload and "_" in payload
    return token


class TestJwtDecoding:
    def test_url_safe_payload_decodes(self):
        """The bug being guarded: such tokens used to read as unreadable, hence expired."""
        token = jwt_with_url_safe_characters(NOW + 3600)
        assert jwt_claims(token)["exp"] == NOW + 3600
        assert jwt_is_usable(token, 60, NOW)

    def test_standard_alphabet_payload_also_decodes(self):
        """Some issuers emit '+' and '/'; a lenient read keeps those tokens usable too."""
        raw = json.dumps({"exp": NOW + 3600, "pad": ">>>???" * 3}).encode()
        payload = base64.b64encode(raw).rstrip(b"=").decode()
        assert "+" in payload and "/" in payload
        assert jwt_expiry(f"eyJhbGciOiJub25lIn0.{payload}.sig") == NOW + 3600

    @pytest.mark.parametrize("length", range(1, 9))
    def test_padding_is_restored_for_every_length(self, length):
        raw = b"x" * length
        segment = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        assert b64url_decode(segment) == raw

    def test_expired_and_within_margin_are_not_usable(self):
        assert not jwt_is_usable(make_jwt({"exp": NOW - 1}), 60, NOW)
        assert not jwt_is_usable(make_jwt({"exp": NOW + 30}), 60, NOW)
        assert jwt_is_usable(make_jwt({"exp": NOW + 61}), 60, NOW)

    def test_no_exp_claim_never_expires(self):
        assert jwt_is_usable(make_jwt({"sub": "tester"}), 60, NOW)

    @pytest.mark.parametrize("token", ["", "eyJ.only-two", "eyJ.!!!.x", "not-a-jwt", None])
    def test_garbage_is_not_usable(self, token):
        assert not jwt_is_usable(token, 60, NOW)


def _cache(tmp_path, ttl=900, clock=lambda: NOW) -> SessionTokenStore:
    return SessionTokenStore("https://login.example.test/_audience", tmp_path, ttl, clock)


class TestTokenFile:
    def test_round_trip_and_expired_token_is_discarded(self, tmp_path):
        cache = _cache(tmp_path)
        good = make_jwt({"exp": NOW + 3600})
        cache.save_token(good)
        assert cache.load_token() == good

        cache.save_token(make_jwt({"exp": NOW + 10}))
        assert cache.load_token() is None
        assert not cache.token_path.exists()

    def test_key_is_sanitised_into_a_file_name(self, tmp_path):
        assert "/" not in _cache(tmp_path).token_path.name

    def test_token_file_is_private(self, tmp_path):
        cache = _cache(tmp_path)
        cache.save_token(make_jwt({"exp": NOW + 3600}))
        assert oct(cache.token_path.stat().st_mode & 0o777) == "0o600"


class TestStorageState:
    def test_fresh_valid_state_is_used(self, tmp_path):
        cache = _cache(tmp_path, clock=lambda: os.path.getmtime(cache.storage_state_path) + 10)
        cache.save_storage_state({"cookies": [], "origins": []})
        assert cache.storage_state_valid()
        assert cache.load_storage_state() == {"cookies": [], "origins": []}

    def test_state_older_than_the_session_is_stale(self, tmp_path):
        cache = _cache(
            tmp_path, ttl=900, clock=lambda: os.path.getmtime(cache.storage_state_path) + 901
        )
        cache.save_storage_state({"cookies": []})
        assert not cache.storage_state_valid()

    def test_truncated_or_concatenated_file_is_treated_as_absent(self, tmp_path):
        """The failure atomic writes prevent: the tail of one write behind another."""
        cache = _cache(tmp_path, clock=lambda: os.path.getmtime(cache.storage_state_path) + 1)
        cache.storage_state_path.parent.mkdir(parents=True, exist_ok=True)
        cache.storage_state_path.write_text('{"cookies": []}{"cookies": [')
        assert not cache.storage_state_valid()

    def test_opaque_token_validity_follows_the_storage_state_age(self, tmp_path):
        age = {"value": 100}
        cache = _cache(
            tmp_path,
            ttl=900,
            clock=lambda: os.path.getmtime(cache.storage_state_path) + age["value"],
        )
        cache.save_storage_state({"cookies": []})
        assert cache.is_usable("SESSION=abc", margin_s=60)
        age["value"] = 850  # within the margin of the session lifetime
        assert not cache.is_usable("SESSION=abc", margin_s=60)

    def test_invalidate_removes_both_files(self, tmp_path):
        cache = _cache(tmp_path)
        cache.save_token("t")
        cache.save_storage_state({})
        cache.invalidate()
        assert not cache.token_path.exists() and not cache.storage_state_path.exists()


def test_atomic_write_leaves_no_temp_files(tmp_path):
    target = tmp_path / "nested" / "state.json"
    write_atomically(target, "{}")
    write_atomically(target, '{"a": 1}')
    assert json.loads(target.read_text()) == {"a": 1}
    assert [p.name for p in target.parent.iterdir()] == ["state.json"]
