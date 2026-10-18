"""UI view models and page pipeline, offline (no web framework, no template engine)."""

from __future__ import annotations

from qaplatform.http.ui import views
from qaplatform.http.ui.pages import COOKIE, csrf_token, ui_handle
from tests.qaplatform._helpers import Api, finish, new_run, push, token_for


def _summary(api, results, expected=None, verdicts=()):
    run_id = new_run(api, expected_count=expected) if expected else new_run(api)
    push(api, run_id, results)
    if verdicts:
        api("POST", f"/projects/default/runs/{run_id}/verdicts", list(verdicts))
    return finish(api, run_id).payload


def test_all_skipped_run_never_reads_as_passed(api):
    summary = _summary(api, [{"test_id": f"t::{i}", "outcome": "skipped"} for i in range(3)], 3)
    head = views.headline(summary)
    assert head["label"] == "FAIL" and head["what"] == "nothing executed"
    assert "pass" not in head["label"].lower()


def test_shortfall_reads_not_assessed(api):
    head = views.headline(_summary(api, [{"test_id": "t::1", "outcome": "passed"}], 5))
    assert head["label"] == "UNJUDGED" and head["tone"] == "warn"


def test_pass_counts_always_come_with_proof_counts(api):
    summary = _summary(
        api,
        [{"test_id": "t::1", "outcome": "passed"}, {"test_id": "t::2", "outcome": "passed"}],
        verdicts=[
            {"test_id": "t::1", "kind": "coverage", "subject": "REQ-1", "verdict": "abstained"}
        ],
    )
    labels = {c["label"]: c["value"] for c in views.qualified_counts(summary)}
    assert labels["passed"] == 2
    assert labels["proved"] == 0 and labels["abstained"] == 1 and labels["not exercised"] == 0
    assert labels["received / declared"] == "2 / undeclared"


def test_open_run_is_provisional(api):
    run_id = new_run(api)
    summary = api("GET", f"/projects/default/runs/{run_id}").payload
    assert "provisional" in views.headline(summary)["label"]
    assert views.runs_page([{"status": "open", "integrity_verdict": None}])[0][
        "integrity_label"
    ].startswith("open")


def test_only_http_links_are_links():
    assert views.safe_link("https://ci.example.test/r/1")["href"] == "https://ci.example.test/r/1"
    assert views.safe_link("javascript:alert(1)")["href"] is None
    assert views.safe_link("reports/trace.zip")["href"] is None


def test_requirements_totals():
    report = {
        "rows": [
            {"requirement": "REQ-1", "status": "proved", "tests": []},
            {"requirement": "REQ-2", "status": "no_tests", "tests": []},
        ]
    }
    page = views.requirements_page(report)
    assert page["totals"] == {"proved": 1, "no_tests": 1}


def test_pages_without_a_session_redirect_to_sign_in(service):
    resp = ui_handle(service, "GET", "/default/runs")
    assert (resp.status, resp.redirect) == (303, "/ui/login")


def test_sign_in_sets_the_cookie_and_bad_tokens_are_refused(service, admin_token):
    ok = ui_handle(service, "POST", "/login", form={"token": admin_token})
    assert ok.redirect == "/ui/" and ok.set_token == admin_token
    bad = ui_handle(service, "POST", "/login", form={"token": "qap_nope"})
    assert bad.status == 401 and bad.set_token is None and COOKIE


def test_run_page_context(service, api, admin_token):
    summary = _summary(api, [{"test_id": "t::1", "outcome": "failed", "message": "boom"}])
    resp = ui_handle(service, "GET", f"/default/runs/{summary['run_id']}", token=admin_token)
    assert resp.template == "run.html"
    assert resp.context["headline"]["verdict"] == summary["integrity"]["verdict"]
    assert resp.context["results"][0]["tone"] == "bad"
    assert resp.context["csrf"] == csrf_token(admin_token)


def test_forms_need_the_csrf_value(service, admin_token):
    assert ui_handle(service, "POST", "/logout", token=admin_token).status == 403
    out = ui_handle(
        service, "POST", "/logout", token=admin_token, form={"csrf": csrf_token(admin_token)}
    )
    assert out.clear_token and out.redirect == "/ui/login"


def test_other_projects_and_permissions(service, store, api):
    store.results.ensure_project("other", "Other")
    tester = token_for(store, "t1", "tester")
    assert ui_handle(service, "GET", "/other/runs", token=tester).status == 404
    assert ui_handle(service, "GET", "/default/audit", token=tester).status == 403
    page = ui_handle(service, "GET", "/default/runs", token=tester)
    assert page.status == 200 and page.context["nav"] == {
        "audit": False,
        "manual": True,
        "sessions": True,
    }
    Api(service, tester)  # the same token works on the API too
