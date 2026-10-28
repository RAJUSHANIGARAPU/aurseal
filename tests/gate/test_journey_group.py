"""Journey-group fail-fast: fingerprinting, timeout exemption, and the pytest behaviour."""

from __future__ import annotations

import pytest

from gate.journey_group import fingerprint, is_timeout, skip_enabled

pytest_plugins = ["pytester"]

# The rewrite warning appears when the outer session has already imported the plugin.
PLUGIN = (
    "-p",
    "gate.pytest_plugin",
    "-W",
    "ignore::pytest.PytestAssertRewriteWarning",
    "-p",
    "no:cacheprovider",
    "-rs",
)


class TimeoutError_(Exception):
    pass


TimeoutError_.__name__ = "TimeoutError"


def _raise_from(path: str, exc: BaseException) -> BaseException:
    code = compile("def step():\n    raise exc\n", path, "exec")
    namespace = {"exc": exc}
    exec(code, namespace)
    try:
        namespace["step"]()
    except BaseException as caught:  # noqa: B036
        return caught
    raise AssertionError("unreachable")


def test_fingerprint_is_the_deepest_page_frame():
    exc = _raise_from("/repo/ui/pages/checkout_page.py", ValueError("locator not found"))
    assert fingerprint(exc) == "checkout_page.py:step:2"
    assert fingerprint(exc, ("ui/flows/",)) is None


def test_fingerprint_walks_the_cause_chain():
    inner = _raise_from("/repo/ui/pages/cart_page.py", KeyError("x"))
    try:
        raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        assert fingerprint(outer) == "cart_page.py:step:2"


def test_plain_assertion_has_no_fingerprint():
    try:
        assert 1 == 2
    except AssertionError as exc:
        assert fingerprint(exc) is None


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (TimeoutError_("waiting"), True),
        (Exception("Timeout 30000ms exceeded."), True),
        (ValueError("element not found"), False),
    ],
)
def test_is_timeout(exc, expected):
    assert is_timeout(exc) is expected


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, False),
        ({"CI": "true"}, True),
        ({"CI": "false"}, False),
        ({"CI": "true", "FAILFAST_JOURNEY_GROUP": "false"}, False),
        ({"FAILFAST_JOURNEY_GROUP": "1"}, True),
    ],
)
def test_skip_enabled(monkeypatch, env, expected):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert skip_enabled() is expected


GROUP = """
import pytest
from shop_ui.pages.cart_page import add_to_cart

pytestmark = pytest.mark.journey_group("checkout")

def test_first():
    add_to_cart("{error}")

def test_second():
    pass

def test_third():
    pass
"""

PAGE = """
class TimeoutError(Exception):
    pass

def add_to_cart(kind):
    if kind == "timeout":
        raise TimeoutError("Timeout 5000ms exceeded.")
    raise LookupError("add-to-cart button not found")
"""


@pytest.fixture(autouse=True)
def page_paths(monkeypatch):
    # The repository's own ui/ package is already imported by the outer session, so the
    # synthetic page objects live in a package of their own.
    monkeypatch.setenv("FAILFAST_PAGE_PATHS", "shop_ui/pages/")


def _group(pytester, error: str):
    pytester.mkpydir("shop_ui")
    pytester.mkpydir("shop_ui/pages")
    (pytester.path / "shop_ui" / "pages" / "cart_page.py").write_text(PAGE)
    pytester.syspathinsert()
    pytester.makepyfile(test_flow=GROUP.replace("{error}", error))


def test_deterministic_break_skips_the_peers(pytester, monkeypatch):
    monkeypatch.setenv("FAILFAST_JOURNEY_GROUP", "true")
    _group(pytester, "missing")
    result = pytester.runpytest(*PLUGIN)
    assert result.parseoutcomes() == {"failed": 1, "skipped": 2}
    result.stdout.fnmatch_lines(
        ["*journey group 'checkout' already failed at cart_page.py:add_to_cart:8*"]
    )


def test_recording_without_skipping_runs_everything(pytester):
    _group(pytester, "missing")
    result = pytester.runpytest(*PLUGIN)
    assert result.parseoutcomes() == {"failed": 1, "passed": 2}


def test_timeout_does_not_gate_the_group(pytester, monkeypatch):
    monkeypatch.setenv("FAILFAST_JOURNEY_GROUP", "true")
    _group(pytester, "timeout")
    assert pytester.runpytest(*PLUGIN).parseoutcomes() == {"failed": 1, "passed": 2}
    monkeypatch.setenv("JOURNEY_GROUP_TOLERATE_TIMEOUTS", "false")
    assert pytester.runpytest(*PLUGIN).parseoutcomes() == {"failed": 1, "skipped": 2}


def test_a_failure_that_passes_on_rerun_does_not_gate(pytester, monkeypatch):
    pytest.importorskip("pytest_rerunfailures")
    monkeypatch.setenv("FAILFAST_JOURNEY_GROUP", "true")
    pytester.mkpydir("shop_ui")
    pytester.mkpydir("shop_ui/pages")
    flag = pytester.path / "attempted"
    (pytester.path / "shop_ui" / "pages" / "cart_page.py").write_text(
        f"from pathlib import Path\n\n"
        f"def add_to_cart():\n"
        f"    p = Path({str(flag)!r})\n"
        f"    if not p.exists():\n"
        f"        p.write_text('')\n"
        f"        raise LookupError('button not found')\n"
    )
    pytester.syspathinsert()
    pytester.makepyfile(
        test_flow="""
        import pytest
        from shop_ui.pages.cart_page import add_to_cart

        pytestmark = pytest.mark.journey_group("checkout")

        def test_first():
            add_to_cart()

        def test_second():
            pass
        """
    )
    result = pytester.runpytest(*PLUGIN, "--reruns", "1")
    assert result.parseoutcomes() == {"passed": 2, "rerun": 1}


def test_ungrouped_tests_are_never_skipped(pytester, monkeypatch):
    monkeypatch.setenv("FAILFAST_JOURNEY_GROUP", "true")
    _group(pytester, "missing")
    pytester.makepyfile(
        test_other="""
        from shop_ui.pages.cart_page import add_to_cart

        def test_a():
            add_to_cart("missing")

        def test_b():
            pass
        """
    )
    result = pytester.runpytest(*PLUGIN, "test_other.py")
    assert result.parseoutcomes() == {"failed": 1, "passed": 1}


def test_grouped_tests_get_an_xdist_group(pytester):
    _group(pytester, "missing")
    items, _ = pytester.inline_genitems(*PLUGIN[:2])
    assert {i.get_closest_marker("xdist_group").kwargs["name"] for i in items} == {
        "journey_group:checkout"
    }


UPSTREAM_PAGE = """
from gate.circuit_breaker import ServerErrorBreaker

def add_to_cart(kind):
    if kind == "upstream":
        breaker = ServerErrorBreaker(
            ["sut.example.test"], upstream_timeout=lambda status, body: "payee.example.test/check"
        )
        breaker.record("api", "POST", "https://sut.example.test/orders", 500, "{}")
    raise LookupError("add-to-cart button not found")
"""


def _upstream_group(pytester, kind: str):
    pytester.mkpydir("shop_ui")
    pytester.mkpydir("shop_ui/pages")
    (pytester.path / "shop_ui" / "pages" / "cart_page.py").write_text(UPSTREAM_PAGE)
    pytester.syspathinsert()
    pytester.makepyfile(test_flow=GROUP.replace("{error}", kind))


def test_a_failure_after_an_upstream_timeout_does_not_gate(pytester, monkeypatch):
    from gate import circuit_breaker

    monkeypatch.setenv("FAILFAST_JOURNEY_GROUP", "true")
    circuit_breaker._reset_upstream()
    _upstream_group(pytester, "upstream")
    result = pytester.runpytest(*PLUGIN)
    assert result.parseoutcomes() == {"failed": 1, "passed": 2}
    assert circuit_breaker.upstream_timeouts() == 1
    assert circuit_breaker.last_upstream_timeout() == "payee.example.test/check"
    circuit_breaker._reset_upstream()


def test_an_unchanged_upstream_count_still_gates(pytester, monkeypatch):
    monkeypatch.setenv("FAILFAST_JOURNEY_GROUP", "true")
    _upstream_group(pytester, "missing")
    assert pytester.runpytest(*PLUGIN).parseoutcomes() == {"failed": 1, "skipped": 2}


def test_the_switch_off_gates_after_an_upstream_timeout_too(pytester, monkeypatch):
    from gate import circuit_breaker

    monkeypatch.setenv("FAILFAST_JOURNEY_GROUP", "true")
    monkeypatch.setenv("JOURNEY_GROUP_TOLERATE_TIMEOUTS", "false")
    _upstream_group(pytester, "upstream")
    assert pytester.runpytest(*PLUGIN).parseoutcomes() == {"failed": 1, "skipped": 2}
    circuit_breaker._reset_upstream()


def test_reset_clears_the_upstream_count():
    from gate import circuit_breaker

    breaker = circuit_breaker.ServerErrorBreaker(
        ["sut.example.test"], upstream_timeout=lambda s, b: "dep.example.test/x"
    )
    breaker.record("api", "GET", "https://sut.example.test/a", 500, "{}")
    assert breaker.upstream_timeouts() == circuit_breaker.upstream_timeouts() >= 1
    breaker.reset()
    assert (circuit_breaker.upstream_timeouts(), circuit_breaker.last_upstream_timeout()) == (
        0,
        None,
    )
