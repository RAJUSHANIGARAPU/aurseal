"""
What the Claude client does when the API says no.

The old client wrapped the whole HTTP call in ``except Exception: pass`` and
fell through to ``claude -p`` — the same prompt again, as a subprocess with a
120-second deadline. A 401, a 429, a DNS blip and a 200 whose JSON we could not
parse were one outcome, each costing a second full execution, and the module
never imported ``logging`` so nothing recorded which had happened. Under
``master_orchestrator``'s eight worker threads a throttle became eight
concurrent two-minute subprocesses.

Two halves again, and the second is the one that keeps this honest. "Never falls
back" is trivially satisfied by a client that never calls anything. The controls
assert the API path still returns text, the CLI fallback still fires on the two
classes it is meant for, and the CLI still produces an answer through stdin — so
a client that had simply gone dead would fail here.

Nothing in this file touches the network or the real `claude` binary: every
subprocess is a recorder that fails the test if it is spawned when it should not
be.
"""

from __future__ import annotations

import logging
import subprocess

import pytest
import requests

from api.llm import base
from api.llm.claude_client import ClaudeLLMClient

KEY = "sk-ant-test"
PROMPT = "classify this drift"


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"content": [{"text": "ok"}]}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _CliRecorder:
    """Stands in for subprocess.run. Records calls; never launches anything."""

    def __init__(self, stdout: str = "cli answer", returncode: int = 0, raises=None):
        self.calls: list[dict] = []
        self._stdout = stdout
        self._returncode = returncode
        self._raises = raises

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": argv, **kwargs})
        if self._raises is not None:
            raise self._raises
        return subprocess.CompletedProcess(
            args=argv, returncode=self._returncode, stdout=self._stdout, stderr="boom"
        )


@pytest.fixture
def cli(monkeypatch):
    """A `claude` binary that exists on PATH but is a recorder, not a process."""
    recorder = _CliRecorder()
    monkeypatch.setattr("api.llm.claude_client.shutil.which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr("api.llm.claude_client.subprocess.run", recorder)
    return recorder


def _api(monkeypatch, response=None, raises=None):
    """Point the client's requests.post at a canned outcome."""

    def fake_post(*_args, **_kwargs):
        if raises is not None:
            raise raises
        return response

    monkeypatch.setattr("api.llm.claude_client.requests.post", fake_post)


class TestAFailedApiCallDoesNotBecomeASecondPaidCall:
    """
    Each case below spawned a 120-second subprocess running the same prompt.
    """

    @pytest.mark.parametrize(
        "status,kind",
        [
            (401, base.AUTH),
            (403, base.AUTH),
            (429, base.RATE_LIMITED),
            (400, base.CLIENT_ERROR),
            (500, base.SERVER_ERROR),
            (503, base.SERVER_ERROR),
        ],
    )
    def test_an_http_error_does_not_reach_the_cli(self, monkeypatch, cli, status, kind):
        _api(monkeypatch, response=_FakeResponse(status_code=status))
        result = ClaudeLLMClient(api_key=KEY).complete_result(PROMPT)

        assert cli.calls == [], f"HTTP {status} escalated into a CLI subprocess"
        assert result.failure is not None
        assert result.failure.kind == kind

    def test_a_request_timeout_does_not_reach_the_cli(self, monkeypatch, cli):
        """The request may already have been accepted and billed."""
        _api(monkeypatch, raises=requests.Timeout("read timed out"))
        result = ClaudeLLMClient(api_key=KEY).complete_result(PROMPT)

        assert cli.calls == []
        assert result.failure is not None
        assert result.failure.kind == base.TIMEOUT

    def test_a_malformed_200_does_not_reach_the_cli(self, monkeypatch, cli):
        """The call succeeded and was billed; our parser is what is wrong."""
        _api(monkeypatch, response=_FakeResponse(payload={"unexpected": "shape"}))
        result = ClaudeLLMClient(api_key=KEY).complete_result(PROMPT)

        assert cli.calls == []
        assert result.failure is not None
        assert result.failure.kind == base.MALFORMED_RESPONSE

    def test_unparseable_json_does_not_reach_the_cli(self, monkeypatch, cli):
        _api(monkeypatch, response=_FakeResponse(payload=ValueError("not json")))
        result = ClaudeLLMClient(api_key=KEY).complete_result(PROMPT)

        assert cli.calls == []
        assert result.failure.kind == base.MALFORMED_RESPONSE


class TestTheFallbackStillFiresWhereItIsMeantTo:
    """
    Positive control for the class above. A client that never called the CLI at
    all would pass every test up there.
    """

    def test_no_api_key_uses_the_cli(self, monkeypatch, cli):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert ClaudeLLMClient(api_key="").complete(PROMPT) == "cli answer"
        assert len(cli.calls) == 1

    def test_an_unreachable_host_uses_the_cli(self, monkeypatch, cli):
        """Nothing was spent and the CLI may route differently."""
        _api(monkeypatch, raises=requests.ConnectionError("name resolution failed"))
        assert ClaudeLLMClient(api_key=KEY).complete(PROMPT) == "cli answer"
        assert len(cli.calls) == 1

    def test_a_healthy_api_call_returns_the_text_and_spawns_nothing(self, monkeypatch, cli):
        _api(monkeypatch, response=_FakeResponse(payload={"content": [{"text": "safe"}]}))
        assert ClaudeLLMClient(api_key=KEY).complete(PROMPT) == "safe"
        assert cli.calls == []

    def test_no_key_and_no_cli_is_named_as_such(self, monkeypatch):
        monkeypatch.setattr("api.llm.claude_client.shutil.which", lambda _: None)
        result = ClaudeLLMClient(api_key="").complete_result(PROMPT)
        assert result.failure.kind == base.NO_CREDENTIALS


class TestAnOperatorCanTellWhichFailureHappened:
    """The module did not import logging at all, so none of this was recorded."""

    @pytest.mark.parametrize(
        "status,kind", [(401, base.AUTH), (429, base.RATE_LIMITED), (500, base.SERVER_ERROR)]
    )
    def test_the_failure_class_is_logged(self, monkeypatch, cli, caplog, status, kind):
        _api(monkeypatch, response=_FakeResponse(status_code=status))
        with caplog.at_level(logging.WARNING, logger="api.llm.claude_client"):
            ClaudeLLMClient(api_key=KEY).complete(PROMPT)

        assert any(
            kind in record.getMessage() for record in caplog.records
        ), f"nothing in the log names the {kind} failure"

    def test_a_healthy_call_logs_no_warning(self, monkeypatch, cli, caplog):
        """Control: the log must stay quiet when the call worked."""
        _api(monkeypatch, response=_FakeResponse())
        with caplog.at_level(logging.WARNING, logger="api.llm.claude_client"):
            ClaudeLLMClient(api_key=KEY).complete(PROMPT)
        assert caplog.records == []


class TestFailuresAreNeverReturnedAsContent:
    """
    ``base.complete()`` documents an empty string on failure. The client returned
    prose — ``"claude CLI timeout (120s)"``, ``"claude CLI error: ..."`` — and
    callers cannot tell that from a model answer.
    """

    def test_a_cli_timeout_returns_no_text(self, monkeypatch):
        monkeypatch.setattr("api.llm.claude_client.shutil.which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr(
            "api.llm.claude_client.subprocess.run",
            _CliRecorder(raises=subprocess.TimeoutExpired(cmd="claude", timeout=120)),
        )
        client = ClaudeLLMClient(api_key="")

        assert client.complete(PROMPT) == ""
        assert client.complete_result(PROMPT).failure.kind == base.CLI_TIMEOUT

    def test_a_cli_error_returns_no_text(self, monkeypatch):
        monkeypatch.setattr("api.llm.claude_client.shutil.which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr(
            "api.llm.claude_client.subprocess.run", _CliRecorder(stdout="", returncode=1)
        )
        client = ClaudeLLMClient(api_key="")

        assert client.complete(PROMPT) == ""
        assert client.complete_result(PROMPT).failure.kind == base.CLI_ERROR

    def test_an_api_error_returns_no_text(self, monkeypatch, cli):
        _api(monkeypatch, response=_FakeResponse(status_code=401))
        assert ClaudeLLMClient(api_key=KEY).complete(PROMPT) == ""

    def test_a_real_answer_is_still_returned_verbatim(self, monkeypatch, cli):
        """Control: emptying everything would satisfy the three tests above."""
        _api(monkeypatch, response=_FakeResponse(payload={"content": [{"text": "semantic"}]}))
        assert ClaudeLLMClient(api_key=KEY).complete(PROMPT) == "semantic"

    def test_ok_separates_an_empty_answer_from_an_outage(self, monkeypatch, cli):
        """
        The whole point of ``complete_result``: a model that legitimately said
        nothing is not the same event as a model that was never reached.
        """
        _api(monkeypatch, response=_FakeResponse(payload={"content": [{"text": ""}]}))
        empty_answer = ClaudeLLMClient(api_key=KEY).complete_result(PROMPT)
        assert empty_answer.ok
        assert empty_answer.text == ""

        _api(monkeypatch, response=_FakeResponse(status_code=429))
        outage = ClaudeLLMClient(api_key=KEY).complete_result(PROMPT)
        assert not outage.ok


class TestThePromptDoesNotTravelInArgv:
    """
    ``["claude", "-p", prompt]`` is readable via ``ps`` by any local user, and
    healer prompts carry page DOM.
    """

    def test_the_prompt_is_not_an_argument(self, monkeypatch, cli):
        secret_dom = "<input name='password' value='hunter2'>"
        ClaudeLLMClient(api_key="").complete(secret_dom)

        argv = cli.calls[0]["argv"]
        assert argv == ["claude", "-p"]
        assert not any(secret_dom in str(part) for part in argv)

    def test_the_prompt_still_reaches_the_process_on_stdin(self, monkeypatch, cli):
        """Control: removing it from argv is worthless if it goes nowhere."""
        ClaudeLLMClient(api_key="").complete(PROMPT)
        assert cli.calls[0]["input"] == PROMPT


class TestTheCliRespectsMaxTokens:
    """
    The CLI takes no token cap, so a caller asking for 20 tokens — the flakiness
    classifier expects one word — could get an essay, which then lands in the
    cost governor's cache and in reports.
    """

    def test_a_runaway_answer_is_capped(self, monkeypatch):
        monkeypatch.setattr("api.llm.claude_client.shutil.which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr(
            "api.llm.claude_client.subprocess.run", _CliRecorder(stdout="x" * 10_000)
        )
        answer = ClaudeLLMClient(api_key="").complete(PROMPT, max_tokens=20)
        assert len(answer) <= 20 * 4

    def test_an_answer_inside_the_budget_is_untouched(self, monkeypatch):
        """Control: a cap that truncates everything would pass the test above."""
        monkeypatch.setattr("api.llm.claude_client.shutil.which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr("api.llm.claude_client.subprocess.run", _CliRecorder(stdout="timing"))
        assert ClaudeLLMClient(api_key="").complete(PROMPT, max_tokens=20) == "timing"

    def test_a_large_budget_is_not_truncated(self, monkeypatch):
        monkeypatch.setattr("api.llm.claude_client.shutil.which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr(
            "api.llm.claude_client.subprocess.run", _CliRecorder(stdout="y" * 5_000)
        )
        assert len(ClaudeLLMClient(api_key="").complete(PROMPT, max_tokens=2048)) == 5_000
