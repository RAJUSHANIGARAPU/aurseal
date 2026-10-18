"""Per-environment login credentials checked before any test (gate.credentials)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from gate.credentials import load_environments, main, preflight

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "pw-not-real-91f2"
SHARED_PASSWORD = "shared-pw-not-real-77"

ENVIRONMENTS = {
    "own": {
        "auth_mode": "ui",
        "credentials": {"username_var": "U", "password_var": "P", "shared": False},
    },
    "pooled": {
        "auth_mode": "password",
        "credentials": {"username_var": "U", "password_var": "P", "shared": True},
    },
    "open": {"auth_mode": "none"},
    "tokened": {"auth_mode": "token"},
}
SHARED = {"SHARED_LOGIN_USERNAME": "shared-user", "SHARED_LOGIN_PASSWORD": SHARED_PASSWORD}


def _run(env: str, environ: dict, capsys) -> tuple[int, str]:
    result = preflight(env, environ, ENVIRONMENTS)
    captured = capsys.readouterr()
    return result.code, result.message + captured.out + captured.err


def test_both_variables_set_passes_and_exports(capsys):
    result = preflight("own", {"U": "alice", "P": PASSWORD}, ENVIRONMENTS)
    assert result.ok
    assert result.exports == {"AUTH_USERNAME": "alice", "AUTH_PASSWORD": PASSWORD}
    assert PASSWORD not in result.message and PASSWORD not in repr(result)


def test_password_missing_names_env_and_variable(capsys):
    code, text = _run("own", {"U": "alice", "P": "  "}, capsys)
    assert code == 1
    assert "'own'" in text and "missing P" in text and "GitHub Environment 'own'" in text


def test_own_credential_required_ignores_the_shared_fallback(capsys):
    code, text = _run("own", dict(SHARED), capsys)
    assert code == 1 and "shared: false" in text
    assert SHARED_PASSWORD not in text


def test_shared_environment_uses_the_fallback(capsys):
    result = preflight("pooled", dict(SHARED), ENVIRONMENTS)
    assert result.ok and result.exports["AUTH_PASSWORD"] == SHARED_PASSWORD
    assert SHARED_PASSWORD not in result.message


def test_auth_mode_none_needs_nothing():
    assert preflight("open", {}, ENVIRONMENTS).ok


def test_token_mode_needs_the_supplied_token():
    assert preflight("tokened", {"SUPPLIED_TOKEN": "t"}, ENVIRONMENTS).ok
    assert preflight("tokened", {}, ENVIRONMENTS).code == 1


def test_unknown_environment_lists_the_known_ones():
    result = preflight("prod", {}, ENVIRONMENTS)
    assert result.code == 2 and "['open', 'own', 'pooled', 'tokened']" in result.message


@pytest.mark.parametrize(
    "env,environ",
    [
        ("own", {"U": "alice", "P": PASSWORD}),
        ("own", {"U": "alice"}),
        ("own", dict(SHARED)),
        ("pooled", dict(SHARED)),
        ("open", {"P": PASSWORD}),
    ],
)
def test_the_cli_never_prints_a_password(tmp_path, capsys, env, environ):
    path = tmp_path / "environments.json"
    path.write_text(__import__("json").dumps(ENVIRONMENTS))
    github_env = tmp_path / "github_env"
    main(
        ["preflight", "--env", env, "--environments", str(path), "--github-env", str(github_env)],
        environ=environ,
    )
    captured = capsys.readouterr()
    assert PASSWORD not in captured.out + captured.err
    assert SHARED_PASSWORD not in captured.out + captured.err


def test_the_committed_environments_declare_credentials():
    for name, block in load_environments().items():
        creds = block["credentials"]
        assert {"username_var", "password_var", "shared"} <= set(creds), name


BROWSER_LANE_JOBS = ("browser", "browser-matrix", "bringup")


def _runs_tests(step: dict) -> bool:
    run = str(step.get("run") or "")
    return "pytest" in run or "gate.lanes bringup" in run or "gate.run_inputs run" in run


def test_every_browser_lane_job_runs_the_preflight_before_its_first_test_step():
    jobs = yaml.safe_load((ROOT / ".github/workflows/tests.yml").read_text())["jobs"]
    for name in BROWSER_LANE_JOBS:
        steps = jobs[name]["steps"]
        first_test = next(i for i, s in enumerate(steps) if _runs_tests(s))
        preflight_at = [
            i for i, s in enumerate(steps) if "gate.credentials preflight" in str(s.get("run"))
        ]
        assert preflight_at and preflight_at[0] < first_test, name
