"""Deployed-version probe, checkout provenance, gateway-retry wiring, ordered locator fallback."""

from __future__ import annotations

import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from core import build_info as dv
from core.base_page import BasePage
from core.browser_resilience import fetch_with_retry, install, make_route_handler
from core.provenance import NOT_BOUND, CheckoutProvenance
from tests.core._log_capture import framework_log


def _fetcher(answers: dict):
    def fetch(path):
        answer = answers.get(path, (404, ""))
        if isinstance(answer, Exception):
            raise answer
        return answer

    return fetch


class TestDeployedVersionProbe:
    def test_first_real_answer_wins(self):
        probe = dv.BuildInfoProbe(
            _fetcher({"/version": (200, json.dumps({"version": "4.2.0", "commit": "abc123"}))})
        )
        version = probe.probe()
        assert version.known and version.value == "4.2.0"
        assert version.describe() == "4.2.0 [version endpoint (GET /version)] — commit abc123"

    def test_falls_back_to_actuator_info(self):
        body = {
            "build": {"version": "1.9.3", "name": "orders"},
            "git": {"commit": {"id": "deadbeef"}},
        }
        probe = dv.BuildInfoProbe(_fetcher({"/actuator/info": (200, json.dumps(body))}))
        assert probe.probe().describe() == (
            "1.9.3 [build endpoint (GET /actuator/info)] — service orders, commit deadbeef"
        )

    def test_absence_is_explicit_and_says_why_each_candidate_missed(self):
        probe = dv.BuildInfoProbe(
            _fetcher(
                {"/version": (200, "<html>"), "/actuator/info": ConnectionError("refused\nmore")}
            )
        )
        version = probe.probe()
        assert not version.known
        text = version.describe()
        assert text.startswith("UNKNOWN — ")
        assert "/version -> HTTP 200 with an unparseable body" in text
        assert "/actuator/info -> unreachable (ConnectionError: refused more)" in text

    @pytest.mark.parametrize(
        "answer, reason",
        [
            ((403, ""), "HTTP 403"),
            ((200, '{"status": "UP"}'), "no version field"),
            ((200, '{"version": "  "}'), "no version field"),
            (None, "no response"),
        ],
    )
    def test_each_miss_is_named(self, answer, reason):
        probe = dv.BuildInfoProbe(
            lambda path: answer, [dv.Candidate("/version", dv.read_version_field)]
        )
        assert reason in probe.probe().describe()

    def test_measured_once_per_process(self):
        dv.reset_for_tests()
        calls = []

        def fetch(path):
            calls.append(path)
            return 200, '{"version": "7"}'

        probe = dv.BuildInfoProbe(fetch)
        assert dv.last_observed().describe().startswith("UNKNOWN — no version probe has run")
        with framework_log() as log:
            assert dv.observe_once(probe).value == "7"
            assert dv.observe_once(probe).value == "7"
        assert calls == ["/version"]
        assert log.text.count("deployed version: 7") == 1
        assert dv.last_observed().value == "7"
        dv.reset_for_tests()


class TestCheckoutProvenance:
    SPEC = CheckoutProvenance(
        prefix="SPEC_REPO", default_sibling="shared-specs", marker_file="openapi.yaml"
    )

    def test_nothing_resolved_is_not_bound(self, tmp_path):
        assert self.SPEC.ref({}, cwd=tmp_path / "here") == NOT_BOUND
        assert self.SPEC.commit({}, cwd=tmp_path / "here") == NOT_BOUND

    def test_explicit_path_is_authoritative_and_never_falls_back(self, tmp_path):
        (tmp_path / "shared-specs" / ".git").mkdir(parents=True)
        environ = {"SPEC_REPO_PATH": str(tmp_path / "missing")}
        assert self.SPEC.checkout(environ, cwd=tmp_path / "here") is None

    def test_marker_file_qualifies_an_explicit_path_without_git(self, tmp_path):
        (tmp_path / "openapi.yaml").write_text("openapi: 3.1.0")
        environ = {"SPEC_REPO_PATH": str(tmp_path), "SPEC_REPO_COMMIT": "0123456789abcdef"}
        assert self.SPEC.ref(environ) == "origin/main"
        assert self.SPEC.commit(environ) == "01234567"

    def test_pinned_checkout_names_no_ref(self, tmp_path):
        (tmp_path / "openapi.yaml").write_text("")
        environ = {"SPEC_REPO_PATH": str(tmp_path), "SPEC_REPO_PINNED": "true"}
        assert self.SPEC.ref(environ) == "pinned checkout"

    @pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
    def test_local_fallback_asks_git(self, tmp_path):
        repo = tmp_path / "shared-specs"
        repo.mkdir()
        run = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)  # noqa: E731
        run("init", "-q")
        run(
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.test",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "x",
        )
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
        ).stdout
        environ = {"SPEC_REPO_PINNED": "true"}
        assert self.SPEC.commit(environ, cwd=tmp_path / "here") == head[:8]
        assert self.SPEC.as_environment(
            "Specs", {"SPEC_REPO_PATH": str(repo), "SPEC_REPO_PINNED": "1"}
        ) == {
            "Specs.Ref": "pinned checkout",
            "Specs.Commit": head[:8],
        }


class _Route:
    def __init__(self, method, url, statuses, fetch_error=None, fulfill_error=None):
        self.request = SimpleNamespace(method=method, url=url)
        self._statuses = list(statuses)
        self._fetch_error = fetch_error
        self._fulfill_error = fulfill_error
        self.calls: list = []

    def fetch(self):
        self.calls.append("fetch")
        if self._fetch_error:
            raise self._fetch_error
        return SimpleNamespace(status=self._statuses.pop(0))

    def fallback(self):
        self.calls.append("fallback")

    def fulfill(self, response):
        if self._fulfill_error:
            raise self._fulfill_error
        self.calls.append(("fulfill", response.status))

    def abort(self, reason):
        self.calls.append("abort")


def _handler(protects=lambda m, u: True, max_retries=3):
    return make_route_handler(
        protects,
        lambda m, u, status: status in (502, 503, 504),
        max_retries,
        0.0,
        sleep=lambda s: None,
    )


class TestGatewayRetryWiring:
    def test_retries_until_a_good_answer_and_hands_it_to_the_page(self):
        route = _Route("GET", "https://app.example.test/api/items", [503, 502, 200])
        _handler()(route)
        assert route.calls == ["fetch", "fetch", "fetch", ("fulfill", 200)]

    def test_gives_up_after_the_budget_and_passes_the_last_answer_on(self):
        route = _Route("GET", "https://app.example.test/api/items", [503] * 5)
        _handler(max_retries=2)(route)
        assert route.calls == ["fetch", "fetch", "fetch", ("fulfill", 503)]

    def test_unprotected_requests_pass_straight_through(self):
        route = _Route("GET", "https://app.example.test/download/report.pdf", [])
        _handler(protects=lambda m, u: "/download" not in u)(route)
        assert route.calls == ["fallback"]

    def test_failed_fetch_resumes_a_get(self):
        route = _Route("GET", "https://app.example.test/api", [], fetch_error=RuntimeError("reset"))
        _handler()(route)
        assert route.calls == ["fetch", "fallback"]

    def test_failed_fetch_never_resends_a_write(self):
        route = _Route(
            "POST", "https://app.example.test/api", [], fetch_error=RuntimeError("reset")
        )
        _handler()(route)
        assert route.calls == ["fetch", "abort"]

    def test_failed_fulfil_aborts(self):
        route = _Route(
            "POST", "https://app.example.test/api", [200], fulfill_error=RuntimeError("closed")
        )
        _handler()(route)
        assert route.calls == ["fetch", "abort"]

    def test_linear_backoff(self):
        sleeps: list[float] = []
        statuses = iter([503, 503, 200])
        outcome = fetch_with_retry(
            lambda: next(statuses), lambda s: s, lambda s: s == 503, 5, 0.5, sleeps.append
        )
        assert (outcome.response, outcome.retries) == (200, 2)
        assert sleeps == [0.5, 1.0]

    def test_install_routes_everything_or_nothing(self):
        routes: list = []
        target = SimpleNamespace(route=lambda pattern, handler: routes.append(pattern))
        install(target, lambda m, u: True, lambda m, u, s: False, max_retries=0)
        assert routes == []
        install(target, lambda m, u: True, lambda m, u, s: False)
        assert routes == ["**/*"]


class _Locator:
    def __init__(self, page, key):
        self.page, self.key = page, key

    @property
    def first(self):
        return self

    def count(self):
        return self.page.present.get(self.key, 0)


class _Page:
    def __init__(self, present):
        self.present = present
        self.calls: list = []

    def get_by_test_id(self, value):
        self.calls.append(("testid", value))
        return _Locator(self, ("testid", getattr(value, "pattern", value)))

    def get_by_label(self, value, exact=True):
        self.calls.append(("label", getattr(value, "pattern", value), exact))
        return _Locator(self, ("label", getattr(value, "pattern", value)))

    def get_by_role(self, role, **kwargs):
        self.calls.append(("role", role, kwargs))
        return _Locator(self, ("role", role))


class TestBasePage:
    LOCATORS = {
        "exact": {"type": "testid", "value": "order-total"},
        "loose": {"type": "label", "value": "^total", "regex": True},
        "button": {"type": "role", "role": "button", "name": "^save$", "regex": True},
    }

    def _page_object(self, tmp_path, page):
        path = tmp_path / "locators.json"
        path.write_text(json.dumps(self.LOCATORS))
        return BasePage(page, None, locator_file=path)

    def test_first_present_key_wins_in_argument_order(self, tmp_path):
        page = _Page({("testid", "order-total"): 1, ("label", "^total"): 3})
        found = self._page_object(tmp_path, page).resolve_first_present("exact", "loose")
        assert found.key == ("testid", "order-total")

    def test_fallback_is_used_only_when_the_exact_match_is_absent(self, tmp_path):
        page = _Page({("label", "^total"): 1})
        assert (
            self._page_object(tmp_path, page).resolve_first_present("exact", "loose").key[0]
            == "label"
        )

    def test_nothing_present_returns_the_last_key(self, tmp_path):
        found = self._page_object(tmp_path, _Page({})).resolve_first_present("exact", "loose")
        assert found.key == ("label", "^total")

    def test_regex_entries_compile_case_insensitively(self, tmp_path):
        page = _Page({})
        self._page_object(tmp_path, page).resolve("button")
        _, _, kwargs = page.calls[0]
        assert kwargs["name"].pattern == "^save$" and kwargs["name"].flags & 2  # re.IGNORECASE

    def test_default_locator_file_still_loads(self):
        assert "search_input" in BasePage(_Page({}), None).locators
