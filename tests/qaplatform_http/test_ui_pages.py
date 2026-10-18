"""The UI through the real app and real templates."""

from __future__ import annotations

import pytest

from qaplatform.http.ui.pages import COOKIE, UI_ROUTES, csrf_token

P = "/api/v1/projects/default"


@pytest.fixture
def seeded(http):
    run = http.post(
        f"{P}/runs", json={"schema_version": "1.0", "name": "nightly", "expected_count": 3}
    ).json()
    rid = run["run_id"]
    http.post(
        f"{P}/runs/{rid}/results",
        json={
            "schema_version": "1.0",
            "results": [
                {
                    "test_id": "cart::<script>alert(1)</script>",
                    "outcome": "failed",
                    "message": "<b>x</b>",
                    "requirements": ["REQ-7"],
                },
                {"test_id": "cart::adds", "outcome": "passed", "requirements": ["REQ-7"]},
            ],
        },
    )
    http.post(
        f"{P}/runs/{rid}/evidence",
        json={
            "schema_version": "1.0",
            "links": [
                {"kind": "report", "uri": "https://ci.example.test/report/1"},
                {"kind": "other", "uri": "javascript:alert(1)"},
            ],
        },
    )
    http.post(f"{P}/runs/{rid}/finish", json={"schema_version": "1.0"})
    return rid


@pytest.fixture
def browser(http, admin_token):
    from fastapi.testclient import TestClient

    client = TestClient(http.app, follow_redirects=False)
    resp = client.post("/ui/login", data={"token": admin_token})
    assert resp.status_code == 303
    assert "httponly" in resp.headers["set-cookie"].lower()
    assert "samesite=strict" in resp.headers["set-cookie"].lower()
    client.cookies.set(COOKIE, admin_token)
    return client


def test_every_read_page_renders(browser, seeded):
    pages = [
        "/ui/",
        "/ui/default/runs",
        f"/ui/default/runs/{seeded}",
        "/ui/default/requirements",
        "/ui/default/history?test_id=cart::adds",
        "/ui/default/releases/1.0",
        "/ui/default/audit",
    ]
    for page in pages:
        resp = browser.get(page)
        assert resp.status_code == 200, page
        assert resp.headers["content-type"].startswith("text/html")
        assert "default-src 'none'" in resp.headers["content-security-policy"]
    assert browser.get("/ui/default").status_code == 303


def test_run_page_states_the_verdict_and_escapes_input(browser, seeded):
    html = browser.get(f"/ui/default/runs/{seeded}").text
    assert "Run trust: <strong>UNJUDGED</strong>" in html
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "<b>x</b>" not in html
    assert 'href="https://ci.example.test/report/1"' in html
    assert 'href="javascript:' not in html
    assert "not exercised" in html and "proved" in html


def test_sign_in_page_and_redirects(http):
    from fastapi.testclient import TestClient

    anon = TestClient(http.app, follow_redirects=False)
    assert anon.get("/ui/login").status_code == 200
    resp = anon.get("/ui/default/runs")
    assert resp.status_code == 303 and resp.headers["location"] == "/ui/login"
    assert anon.post("/ui/login", data={"token": "qap_wrong"}).status_code == 401


def test_another_projects_viewer_gets_404_and_no_forms(http, browser, db_path):
    from fastapi.testclient import TestClient

    from qaplatform.auth import tokens
    from qaplatform.store import PlatformStore

    http.post("/api/v1/projects", json={"schema_version": "1.0", "key": "other"})
    store = PlatformStore.open(db_path)
    store.ensure_user("viewer1")
    store.scoped("other").grant("viewer1", "viewer", "test")
    raw, _ = tokens.issue(store, user_id="viewer1", project_id="other", role="viewer")
    store.close()
    viewer = TestClient(http.app, follow_redirects=False)
    viewer.cookies.set(COOKIE, raw)
    assert viewer.get("/ui/default/runs").status_code == 404
    page = viewer.get("/ui/other/runs")
    assert page.status_code == 200
    forms = page.text.count("<form")
    assert forms == 2  # the run filter and sign-out: nothing that writes data
    assert viewer.get("/ui/other/audit").status_code == 403


def test_logout_needs_csrf(browser, admin_token):
    assert browser.post("/ui/logout").status_code == 403
    out = browser.post("/ui/logout", data={"csrf": csrf_token(admin_token)})
    assert out.status_code == 303


def test_every_ui_route_is_mounted(http):
    mounted = {(m, r.path) for r in http.app.routes if r.path.startswith("/ui") for m in r.methods}
    for route in UI_ROUTES:
        assert (route.method, "/ui" + route.path) in mounted, route.name


def test_manual_runner_through_the_forms(browser, admin_token):
    csrf = csrf_token(admin_token)
    spec = "## Scenario Login\n### Steps\n1. Open the page\n2. Sign in\n### Tags\nREQ-3\n"
    started = browser.post(
        "/ui/default/manual", data={"csrf": csrf, "name": "smoke by hand", "spec": spec}
    )
    assert started.status_code == 303
    run_url = started.headers["location"]
    run_page = browser.get(run_url).text
    assert "smoke by hand" in run_page and 'data-testid="execution-link"' in run_page
    run_id = run_url.rsplit("/", 1)[1]
    doc = browser.get(
        f"/api/v1/projects/default/manual-runs/{run_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    ).json()
    ex = doc["executions"][0]["id"]
    assert browser.get(f"{run_url}/{ex}").status_code == 200
    for n in (1, 2):
        resp = browser.post(
            f"{run_url}/{ex}/steps/{n}",
            data={
                "csrf": csrf,
                "status": "pass",
                "actual": "ok",
                "evidence_uri": f"https://files.example.test/{n}.png",
            },
        )
        assert resp.status_code == 303
    assert browser.post(f"{run_url}/{ex}/finish", data={"csrf": csrf}).status_code == 303
    done = browser.post(f"{run_url}/finish", data={"csrf": csrf})
    assert done.headers["location"] == f"/ui/default/runs/{run_id}"
    html = browser.get(done.headers["location"]).text
    assert "Run trust: <strong>PASS</strong>" in html
    assert browser.post(f"{run_url}/{ex}/finish", data={"csrf": "forged"}).status_code == 403


def test_exploratory_session_through_the_forms(browser, admin_token):
    csrf = csrf_token(admin_token)
    started = browser.post(
        "/ui/default/sessions",
        data={
            "csrf": csrf,
            "charter": "Probe search with <odd> input",
            "requirements": "REQ-8",
            "timebox_min": "20",
        },
    )
    assert started.status_code == 303
    url = started.headers["location"]
    page = browser.get(url).text
    assert "Probe search with &lt;odd&gt; input" in page and "recorded" in page
    note = browser.post(
        f"{url}/notes",
        data={"csrf": csrf, "kind": "bug", "text": "empty result page", "defect_key": "BUG-9"},
    )
    assert note.status_code == 303
    ended = browser.post(f"{url}/end", data={"csrf": csrf, "debrief": "covered filters"})
    assert ended.status_code == 303
    html = browser.get(url).text
    assert "Bugs found" in html and "BUG-9" in html and "covered filters" in html
    assert 'data-testid="note-add"' not in html  # an ended session takes no more notes
    assert browser.get("/ui/default/sessions").status_code == 200
    bad = browser.post(
        "/ui/default/sessions", data={"csrf": csrf, "charter": "x", "timebox_min": "soon"}
    )
    assert bad.status_code == 400
