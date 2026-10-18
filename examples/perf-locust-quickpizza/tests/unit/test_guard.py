"""The target guard: load and stress never reach a remote host by accident."""

from __future__ import annotations

import pytest

from perf_locust_quickpizza.guard import is_local, main, refusal

pytestmark = pytest.mark.offline


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("http://localhost:3333", id="localhost"),
        pytest.param("http://127.0.0.1:8080/api", id="ipv4-loopback"),
        pytest.param("http://[::1]:3333", id="ipv6-loopback"),
        pytest.param("http://app.localhost", id="dot-localhost"),
        pytest.param("http://host.docker.internal:3333", id="docker-host"),
        pytest.param("http://target:3333", id="compose-service"),
    ],
)
def test_local_targets_are_recognised(url):
    assert is_local(url)


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("https://shop.example.test", id="public-name"),
        pytest.param("http://10.0.0.5:8080", id="private-network"),
        pytest.param("http://localhost.example.test", id="lookalike"),
        pytest.param("not a url", id="garbage"),
    ],
)
def test_remote_targets_are_not_local(url):
    assert not is_local(url)


def test_smoke_may_run_against_a_remote_host():
    assert refusal("smoke", "https://shop.example.test", {}) is None


@pytest.mark.parametrize("profile", ["load", "stress"])
def test_load_profiles_refuse_a_remote_host(profile):
    reason = refusal(profile, "https://shop.example.test", {})
    assert reason is not None
    assert "ALLOW_REMOTE_LOAD=1" in reason


@pytest.mark.parametrize("profile", ["load", "stress"])
def test_load_profiles_run_against_a_local_host(profile):
    assert refusal(profile, "http://localhost:3333", {}) is None


@pytest.mark.parametrize(
    ("value", "allowed"),
    [
        pytest.param("1", True, id="one-allows"),
        pytest.param("true", False, id="only-the-exact-value-1"),
        pytest.param("", False, id="empty"),
    ],
)
def test_remote_load_needs_the_explicit_override(value, allowed):
    reason = refusal("load", "https://shop.example.test", {"ALLOW_REMOTE_LOAD": value})
    assert (reason is None) is allowed


@pytest.mark.parametrize(
    ("argv", "code"),
    [
        pytest.param(["smoke", "https://shop.example.test"], 0, id="smoke-remote"),
        pytest.param(["load", "https://shop.example.test"], 1, id="load-remote"),
        pytest.param(["soak", "http://localhost:3333"], 1, id="unknown-profile"),
        pytest.param(["load", "ftp://localhost"], 1, id="not-http"),
        pytest.param(["load"], 2, id="missing-url"),
    ],
)
def test_command_line_exit_codes(argv, code, capsys):
    assert main(argv, environ={}) == code
    if code:
        assert capsys.readouterr().err.strip()
