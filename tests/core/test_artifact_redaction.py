"""Masking session credentials and personal data in archived traces and logs."""

from __future__ import annotations

import http.server
import json
import logging
import threading
import time
import zipfile
from pathlib import Path

import pytest

from checks._browser import BrowserUnavailable, browser_context
from contract_testing.redaction import REDACTED
from core.artifact_policy import finish_trace
from core.artifact_redaction import (
    RedactingFilter,
    cookie_values,
    credentials,
    redact,
    redact_trace,
)

# Built at run time so no literal in this file looks like a live secret.
SESSION = "sess" + "0123456789abcdef" * 2
CSRF = "csrf-" + "z9y8x7w6" * 3
JWT = ".".join(["eyJhbGciOiJIUzI1NiJ9", "eyJzdWIiOiJ0ZXN0LXVzZXIifQ", "c2lnbmF0dXJlLXZhbHVl"])


def _iban(country: str, bban: str) -> str:
    """An IBAN with correct check digits, computed so the file holds no literal one."""
    digits = "".join(str(int(c, 36)) for c in bban + country + "00")
    return f"{country}{98 - int(digits) % 97:02d}{bban}"


def _zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


def _members(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _event(payload: dict) -> bytes:
    return (json.dumps(payload) + "\n").encode()


class TestTokens:
    def test_oauth_parameters_are_masked(self):
        url = "https://app.example.test/cb?code=abc123def&state=xyz789&keep=1&access_token=tok12345"
        out = credentials(url)
        assert "abc123def" not in out and "xyz789" not in out and "tok12345" not in out
        assert f"code={REDACTED}" in out and "keep=1" in out

    def test_presigned_url_keeps_its_path(self):
        url = (
            "https://bucket.example.test/reports/2026/run.html?X-Amz-Algorithm=AWS4-HMAC-SHA256"
            "&X-Amz-Credential=AKIDEXAMPLE%2F20260101%2Feu%2Fs3&X-Amz-Signature=deadbeef0123"
            "&X-Amz-Security-Token=FwoGZXIvYXdz&X-Amz-Expires=300"
        )
        out = credentials(url)
        assert "bucket.example.test/reports/2026/run.html?" in out
        for secret in ("AKIDEXAMPLE", "deadbeef0123", "FwoGZXIvYXdz"):
            assert secret not in out
        assert "X-Amz-Expires=300" in out

    def test_bearer_and_jwt_are_masked(self):
        assert JWT not in credentials(f"token is {JWT} here")
        out = credentials("Authorization: Bearer abcdefgh12345678")
        assert "abcdefgh12345678" not in out and "Authorization" in out

    def test_header_log_line_is_masked(self):
        line = f'{{"message":"  cookie: sid={SESSION}; theme=dark"}}'
        out = credentials(line)
        assert SESSION not in out and out.endswith(f'{REDACTED}"}}')

    def test_identifiers_are_left_alone(self):
        text = (
            "order 8f14e45f-ceea-467f-a1ab-7a0b1c2d3e4f"
            " sha 356a192b7913b04c54574d18c28d46e6395428ab"
            " error_code=42 version=1.2.3 statement=ok /api/v1/codes/17"
        )
        assert redact(text) == text

    def test_a_20kb_cookie_value_is_linear(self):
        big = "A" * 20_000
        for text in (
            f"cookie: sid={big}",
            f"x@{big}",
            big + "@",
            "a" * 20_000 + "=1",
            f"?code={big}",
        ):
            start = time.perf_counter()
            redact(text)
            assert time.perf_counter() - start < 1.0


class TestPersonalData:
    def test_email_keeps_the_domain(self):
        assert redact("mail jane.doe+x@mail.example.test now") == "mail ***@mail.example.test now"
        assert redact("ends jane@example.test.") == "ends ***@example.test."

    def test_credentials_leaves_personal_data(self):
        assert credentials("jane@example.test") == "jane@example.test"

    def test_iban_keeps_country_and_last_four(self):
        iban = _iban("GB", "WEST12345698765432")
        out = redact(f"pay {iban} today")
        assert iban not in out
        assert out == f"pay GB{'*' * (len(iban) - 6)}{iban[-4:]} today"

    def test_spaced_iban_is_masked_and_a_lookalike_is_not(self):
        iban = _iban("DE", "370400440532013000")
        spaced = " ".join(iban[i : i + 4] for i in range(0, len(iban), 4))
        assert iban[-8:-4] not in redact(spaced)
        lookalike = "NL00ABCD0000000000"
        assert redact(lookalike) == lookalike


class TestTraceRewrite:
    def _trace(self, tmp_path: Path) -> Path:
        network = _event(
            {
                "type": "resource-snapshot",
                "snapshot": {
                    "request": {
                        "headers": [
                            {"name": "Cookie", "value": f"sid={SESSION}"},
                            {"name": "X-CSRF-Token", "value": CSRF},
                            {"name": "Accept", "value": "text/html"},
                        ],
                        "cookies": [{"name": "sid", "value": SESSION}],
                    },
                    "response": {
                        "headers": [{"name": "set-cookie", "value": f"sid={SESSION}; Path=/"}],
                        "cookies": [{"name": "other", "value": "short"}],
                    },
                },
            }
        )
        events = b"".join(
            [
                _event({"type": "log", "message": f"  cookie: sid={SESSION}"}),
                _event({"type": "before", "params": {"note": f"typed {SESSION} somewhere"}}),
                _event({"type": "log", "message": "  accept: text/html"}),
            ]
        )
        return _zip(
            tmp_path / "trace.zip",
            {
                "trace.network": network,
                "0-trace.trace": events,
                "resources/a.json": json.dumps({"auth": f"Bearer {JWT}", "s": SESSION}).encode(),
                "resources/b.html": f"<p>{SESSION}</p>".encode(),
                "resources/c.txt": f"note {SESSION}".encode(),
                "resources/shot.jpeg": b"\xff\xd8" + SESSION.encode() + b"\x00\x01",
            },
        )

    def test_known_values_and_header_shapes_are_masked(self, tmp_path):
        path = self._trace(tmp_path)
        assert redact_trace(path, [SESSION]) is True
        members = _members(path)
        network = json.loads(members["trace.network"])["snapshot"]
        headers = {h["name"]: h["value"] for h in network["request"]["headers"]}
        assert headers["Cookie"] == REDACTED and headers["X-CSRF-Token"] == REDACTED
        assert headers["Accept"] == "text/html"
        assert network["request"]["cookies"] == [{"name": "sid", "value": REDACTED}]
        assert network["response"]["headers"][0]["value"] == REDACTED
        assert network["response"]["cookies"][0]["value"] == REDACTED

    def test_header_log_line_and_known_value_in_any_event(self, tmp_path):
        path = self._trace(tmp_path)
        redact_trace(path)  # shape-based only: no known values supplied
        lines = [json.loads(x) for x in _members(path)["0-trace.trace"].splitlines()]
        assert lines[0]["message"] == f"  cookie: {REDACTED}"
        assert lines[2]["message"] == "  accept: text/html"
        redact_trace(self._trace(tmp_path), [SESSION])
        text = _members(tmp_path / "trace.zip")["0-trace.trace"].decode()
        assert SESSION not in text

    def test_text_resources_lose_values_and_tokens(self, tmp_path):
        path = self._trace(tmp_path)
        redact_trace(path, [SESSION])
        members = _members(path)
        assert SESSION.encode() not in members["resources/a.json"]
        assert JWT.encode() not in members["resources/a.json"]
        assert SESSION.encode() not in members["resources/b.html"]
        assert SESSION.encode() not in members["resources/c.txt"]

    def test_binaries_are_copied_unchanged(self, tmp_path):
        path = self._trace(tmp_path)
        before = _members(path)["resources/shot.jpeg"]
        redact_trace(path, [SESSION])
        assert _members(path)["resources/shot.jpeg"] == before

    def test_a_20kb_value_is_masked_in_a_trace(self, tmp_path):
        value = "k" * 20_000
        path = _zip(
            tmp_path / "big.zip",
            {"t.trace": _event({"type": "log", "message": f"v {value}"}), "n.network": b""},
        )
        start = time.perf_counter()
        assert redact_trace(path, [value]) is True
        assert time.perf_counter() - start < 2.0
        assert value.encode() not in _members(path)["t.trace"]

    def test_a_corrupt_zip_is_deleted(self, tmp_path):
        path = tmp_path / "bad.zip"
        path.write_bytes(b"not a zip " + SESSION.encode())
        assert redact_trace(path, [SESSION]) is False
        assert not path.exists()
        assert not list(tmp_path.iterdir())

    def test_a_member_that_cannot_be_rewritten_deletes_the_trace(self, tmp_path):
        path = _zip(tmp_path / "t.zip", {"t.trace": b"\xff\xfe not utf-8 \x80"})
        assert redact_trace(path) is False
        assert not path.exists()

    def test_unparseable_event_lines_still_lose_known_values(self, tmp_path):
        path = _zip(tmp_path / "t.zip", {"t.trace": f"garbage {SESSION} {{".encode()})
        assert redact_trace(path, [SESSION]) is True
        assert SESSION.encode() not in _members(path)["t.trace"]

    def test_short_values_are_not_hunted(self, tmp_path):
        path = _zip(tmp_path / "t.zip", {"t.trace": _event({"type": "log", "message": "a b"})})
        redact_trace(path, ["a"])
        assert json.loads(_members(path)["t.trace"])["message"] == "a b"


class _Context:
    def __init__(self, cookies, fail=False) -> None:
        self._cookies, self._fail = cookies, fail
        self.tracing = self

    def cookies(self):
        if self._fail:
            raise RuntimeError("closed")
        return self._cookies

    def stop(self, path=None):
        if path:
            _zip(Path(path), {"t.trace": _event({"type": "log", "message": f"sid {SESSION}"})})


class TestWiring:
    def test_cookie_values_survive_a_closed_context(self):
        assert cookie_values(_Context([{"value": "x"}], fail=True)) == []
        assert cookie_values(_Context([{"value": "x"}, {"value": ""}])) == ["x"]

    def test_finish_trace_redacts_what_it_writes(self, tmp_path):
        context = _Context([{"name": "sid", "value": SESSION}])
        written = finish_trace(context, True, tmp_path / "t.zip")
        assert written == tmp_path / "t.zip"
        assert SESSION.encode() not in _members(written)["t.trace"]

    def test_finish_trace_deletes_when_redaction_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.artifact_policy.redact_trace", lambda *a, **k: False)
        assert finish_trace(_Context([]), True, tmp_path / "t.zip") is None

    def test_log_records_are_masked_before_formatting(self):
        record = logging.LogRecord("n", logging.INFO, __file__, 1, "got %s", (JWT,), None)
        assert RedactingFilter().filter(record) is True
        assert JWT not in record.getMessage()


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server API
        body = b"<html><body><p>hello</p></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence
        pass


def _count(path: Path, value: str) -> int:
    needle = value.encode()
    return sum(data.count(needle) for data in _members(path).values())


def test_a_real_browser_trace_holds_no_cookie_value_after_redaction(tmp_path):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    value = "live" + "Q7w3E9r1" * 6
    trace = tmp_path / "trace.zip"
    try:
        try:
            ctx_manager = browser_context()
            context = ctx_manager.__enter__()
        except BrowserUnavailable as exc:
            pytest.skip(f"no browser: {exc}")
        try:
            context.add_cookies([{"name": "sid", "value": value, "url": base}])
            context.tracing.start(screenshots=False, snapshots=True)
            page = context.new_page()
            page.goto(f"{base}/?code=oauthcode123")
            values = cookie_values(context)
            context.tracing.stop(path=str(trace))
        finally:
            ctx_manager.__exit__(None, None, None)
    finally:
        server.shutdown()
        server.server_close()
    assert values == [value]
    assert _count(trace, value) > 0, "the trace should hold the cookie before redaction"
    assert redact_trace(trace, values) is True
    assert _count(trace, value) == 0
    assert _count(trace, "oauthcode123") == 0
