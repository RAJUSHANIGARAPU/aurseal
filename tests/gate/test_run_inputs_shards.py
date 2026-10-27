"""Sharding-pilot inputs and externally triggered post-deploy runs (gate.run_inputs)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
import yaml

from gate.run_inputs import (
    RunInputsError,
    main,
    plan,
    post_deploy_plan,
    shard_argv,
    shard_env,
)

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
ENVS = ["local", "qa", "staging"]


def _plan(context=None, **values):
    return plan(values, environments=ENVS, root=ROOT, context=context or {})


# ---------------------------------------------------------------- shards


def test_one_shard_changes_nothing():
    assert _plan(shards="1").argv == _plan().argv and _plan().shards == 1


@pytest.mark.parametrize(
    ("values", "context", "message"),
    [
        ({"seed_mode": "reuse"}, {}, "needs seed_mode='fresh'"),
        ({"selection": "tests/test_x.py::test_a"}, {}, "a one-off selection is not sharded"),
        ({}, {"GITHUB_REF": "refs/heads/bringup/tests/x.py"}, "bring-up ref"),
        ({}, {"RELEASE_CANDIDATE": "rc-7"}, "release candidate"),
        ({"shard_users": "a,b"}, {}, r"names 2 login\(s\) for 3 shards"),
    ],
)
def test_a_sharded_run_refuses(values, context, message):
    with pytest.raises(RunInputsError, match=message):
        _plan(context, shards="3", **values)


def test_one_shard_refuses_a_login_list():
    with pytest.raises(RunInputsError, match="shards=1"):
        _plan(shard_users="a")


def test_unknown_shard_count_is_refused():
    with pytest.raises(RunInputsError, match="shards"):
        _plan(shards="8")


def test_each_shard_gets_its_own_variables_and_a_proportional_floor():
    planned = _plan(shards="3", shard_users="u1,u2,u3")
    env = shard_env(planned, 2, 3, environ={"RUN_TRUST_MIN_EXECUTED": "10"})
    assert env == {
        "TEST_AUTH_CACHE_DIR": "reports/auth-cache/w2",
        "TEST_WORKER_PREFIX": "w2",
        "RUN_TRUST_MIN_EXECUTED": "4",  # ceil(10 / 3)
        "LOGIN_USERNAME": "u2",
    }
    assert "LOGIN_USERNAME" not in shard_env(_plan(shards="2"), 1, 2, environ={})
    assert shard_env(_plan(shards="2"), 1, 2, environ={})["RUN_TRUST_MIN_EXECUTED"] == "5"
    with pytest.raises(RunInputsError, match="does not match"):
        shard_env(planned, 1, 2, environ={})


def test_shard_command_is_the_gate_command_plus_the_shard_plugin():
    planned = _plan(shards="2")
    argv = shard_argv(planned, 2, 2)
    assert argv[: argv.index("-p")] == [
        a if not a.startswith("--junitxml") else "--junitxml=reports/junit-shard-2.xml"
        for a in planned.argv
    ]
    assert argv[-4:] == ["-p", "gate.pytest_shard", "--shard=2/2", "--shard-dir=reports/shards"]


def test_run_with_shard_spawns_the_shard_command_and_environment():
    spawned = []
    code = main(
        ["run", "--shard", "1/2"],
        environ={"INPUT_SHARDS": "2", "INPUT_SHARD_USERS": "one,two"},
        spawn=lambda argv, env: spawned.append((argv, env)) or 0,
        out=io.StringIO(),
    )
    assert code == 0
    argv, env = spawned[0]
    assert "--shard=1/2" in argv and env["LOGIN_USERNAME"] == "one"
    assert env["TEST_WORKER_PREFIX"] == "w1"


def test_run_with_a_bad_shard_is_an_input_error():
    out = io.StringIO()
    assert main(["run", "--shard", "3/2"], environ={"INPUT_SHARDS": "2"}, out=out) == 2


def test_plan_shards_writes_the_plan_and_the_matrix(tmp_path):
    summary, output = tmp_path / "summary", tmp_path / "output"
    ids = "\n".join(f"tests/test_x.py::test_{n}" for n in range(5)) + "\n\n5 tests collected"
    args = ["plan-shards", "--summary", str(summary), "--github-output", str(output)]
    code = main(args, environ={"INPUT_SHARDS": "2"}, out=io.StringIO(), collect=lambda _: ids)
    assert code == 0
    assert "indices=[1, 2]" in output.read_text()
    text = summary.read_text()
    assert "5 tests over 2 shards" in text and "shard 2/2" in text


def test_plan_shards_refuses_an_empty_collection_and_invalid_inputs(tmp_path):
    out = io.StringIO()
    env = {"INPUT_SHARDS": "2"}
    assert main(["plan-shards"], environ=env, out=out, collect=lambda _: "") == 2
    bad = {**env, "INPUT_SEED_MODE": "reuse"}
    assert main(["plan-shards"], environ=bad, out=out, collect=lambda _: "x::y") == 2


# ---------------------------------------------------------------- post-deploy


def _deploy(event="repository_dispatch", **payload):
    return post_deploy_plan(event, payload, environments=ENVS)


def test_post_deploy_runs_the_preset_without_reruns():
    run = _deploy(env="staging", preset="regression", source="deployer", ref="v2.3.1")
    argv = run.plan.argv
    assert argv[argv.index("--reruns") + 1] == "0"
    assert "--junitxml=reports/junit-post-deploy.xml" in argv
    assert "regression" in argv[argv.index("-m") + 1]
    assert run.plan.env["TEST_ENV"] == "staging"
    assert run.description == "post-deploy staging | triggered by deployer @ v2.3.1"


def test_post_deploy_defaults_and_manual_label():
    run = _deploy("workflow_dispatch", env="staging")
    assert run.source == "manual" and "smoke" in run.plan.argv[2]


@pytest.mark.parametrize(
    ("event", "payload", "message"),
    [
        ("push", {"env": "staging"}, "cannot start a post-deploy run"),
        ("schedule", {"env": "staging"}, "cannot start a post-deploy run"),
        ("repository_dispatch", {"env": "prod", "source": "d"}, "is not declared"),
        ("repository_dispatch", {"source": "d"}, "needs env"),
        ("repository_dispatch", {"env": "staging"}, "needs source"),
        ("repository_dispatch", {"env": "qa", "preset": "regression", "source": "d"}, "smoke"),
        ("repository_dispatch", {"env": "staging", "preset": "nightly", "source": "d"}, "preset"),
        ("repository_dispatch", {"env": "staging", "source": "a b"}, "outside a label"),
        ("repository_dispatch", {"env": "staging", "source": "d", "extra": "x"}, "unknown"),
    ],
)
def test_post_deploy_refuses(event, payload, message):
    with pytest.raises(RunInputsError, match=message):
        _deploy(event, **payload)


def test_shared_environment_allows_smoke():
    assert _deploy(env="qa", preset="smoke", source="deployer").plan.env["TEST_ENV"] == "qa"


def test_post_deploy_cli_validates_exports_and_executes(tmp_path):
    output, env_file = tmp_path / "out", tmp_path / "env"
    environ = {
        "GITHUB_EVENT_NAME": "repository_dispatch",
        "INPUT_ENV": "staging",
        "INPUT_SOURCE": "deployer",
        "INPUT_REF": "v1",
    }
    args = ["post-deploy", "--github-output", str(output), "--github-env", str(env_file)]
    spawned = []
    spawn = lambda argv, env: spawned.append(argv) or 0  # noqa: E731
    assert main(args, environ=environ, out=io.StringIO(), spawn=spawn) == 0
    assert "description=post-deploy staging | triggered by deployer @ v1" in output.read_text()
    assert "TEST_ENV<<" in env_file.read_text() and spawned == []
    assert main([*args, "--execute"], environ=environ, out=io.StringIO(), spawn=spawn) == 0
    assert "--reruns" in spawned[0]
    refused = {**environ, "GITHUB_EVENT_NAME": "push"}
    assert main(["post-deploy"], environ=refused, out=io.StringIO()) == 2


# ---------------------------------------------------------------- the workflows


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def test_post_deploy_workflow_triggers_queue_and_refusal_path():
    flow = _load("post-deploy.yml")
    on = flow.get("on", flow.get(True))
    assert set(on) == {"repository_dispatch", "workflow_dispatch"}
    assert on["repository_dispatch"]["types"] == ["deployed"]
    assert set(on["workflow_dispatch"]["inputs"]) == {"env", "preset", "source", "ref"}
    assert flow["concurrency"]["group"].startswith("post-deploy-")
    assert flow["concurrency"]["cancel-in-progress"] is False
    jobs = flow["jobs"]
    assert "environment" not in jobs["validate"]  # refused before any environment is touched
    assert jobs["notify"]["if"] == "always()" and set(jobs["notify"]["needs"]) == {
        "validate",
        "run",
    }
    steps = " ".join(str(s.get("run", "")) for s in jobs["run"]["steps"])
    assert "gate.run_inputs post-deploy --execute" in steps
    assert "gate.evidence stage" in steps


def test_sharding_is_wired_into_the_gate_workflow():
    flow = _load("tests.yml")
    inputs = flow[True]["workflow_dispatch"]["inputs"]
    assert inputs["shards"]["options"] == ["1", "2", "3", "4"]
    assert inputs["shards"]["default"] == "1"
    jobs = flow["jobs"]
    assert "(inputs.shards || '1') == '1'" in jobs["browser"]["if"]
    assert "fromJSON(needs.shard-plan.outputs.indices)" in str(
        jobs["browser-shard"]["strategy"]["matrix"]["k"]
    )
    shard_steps = " ".join(str(s.get("run", "")) for s in jobs["browser-shard"]["steps"])
    assert 'gate.run_inputs run --shard "$SHARD"' in shard_steps
    assert "LOGIN_PASSWORD_{0}" in json.dumps(jobs["browser-shard"])
    merge_steps = " ".join(str(s.get("run", "")) for s in jobs["browser-shard-merge"]["steps"])
    assert 'gate.merge --manifests reports/shards --shards "$SHARDS"' in merge_steps
    assert "gate.integrity --lane browser --merge" in merge_steps
    for job in ("publish", "notify"):
        assert {"browser-shard", "browser-shard-merge"} <= set(jobs[job]["needs"])
