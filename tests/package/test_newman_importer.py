"""Newman importer: the json reporter's run summary, one record per request and iteration."""

from __future__ import annotations

import io
from importlib.metadata import entry_points
from pathlib import Path

import pytest

from testkeel.importers import ReportError, registry
from testkeel.model.v1 import Status

NEWMAN = Path(__file__).parent / "fixtures" / "newman.json"


def _records():
    return {(r.suite, r.name, r.variant): r for r in registry.parse_file(NEWMAN)}


def test_it_is_sniffed_and_reimports_keep_identities():
    assert registry.detect(NEWMAN.read_bytes()[:4096]).name == "newman"
    first = [r.identity for r in registry.parse_file(NEWMAN)]
    assert len(first) == len(set(first)) == 9
    for _ in range(19):
        assert [r.identity for r in registry.parse_file(NEWMAN)] == first


def test_identity_folders_iterations_and_environment():
    records = _records()
    create = records[("Orders", "create order", "iteration-1")]
    assert (create.framework, create.path, create.configuration, create.explicit_id) == (
        "newman",
        "ACME Shop API",
        "staging",
        "SHOP-21",
    )
    gets = [r for r in records.values() if r.name == "get order"]
    assert sorted(r.variant for r in gets) == ["iteration-1", "iteration-2"]
    assert len({r.fingerprint for r in gets}) == 1
    assert records[("", "health", "iteration-1")].suite == ""


def test_statuses_messages_and_durations():
    records = _records()
    failed = records[("Orders", "get order", "iteration-1")]
    assert (failed.status, failed.message) == (
        Status.FAILED,
        "total is 30: expected 31 to deeply equal 30",
    )
    assert failed.duration_ms == 80
    assert records[("Orders", "get order", "iteration-2")].status is Status.PASSED
    down = records[("", "health", "iteration-1")]
    assert (down.status, down.message) == (Status.BROKEN, "connect ECONNREFUSED 127.0.0.1:80")
    script = records[("", "refund", "iteration-1")]
    assert (script.status, script.message) == (
        Status.BROKEN,
        "testScript: body is not defined",
    )
    assert records[("", "refund", "iteration-2")].status is Status.PASSED  # no tests
    assert records[("", "legacy export", "iteration-1")].status is Status.SKIPPED


def test_a_request_repeated_in_one_iteration_is_attempts_of_one_record():
    health = _records()[("", "health", "iteration-2")]
    assert [a.status for a in health.attempts] == [Status.FAILED, Status.PASSED]
    assert health.flaky_in_run and health.duration_ms == 22


def test_a_single_iteration_has_no_variant():
    body = (
        b'{"collection": {"info": {"name": "c"}, "item": []}, "run": {"executions": ['
        b'{"cursor": {"iteration": 0, "cycles": 1}, "item": {"id": "1", "name": "ping"},'
        b' "response": {"responseTime": 5}}]}}'
    )
    (record,) = registry.parse(io.BytesIO(body), format="newman")
    assert (record.name, record.variant, record.status) == ("ping", None, Status.PASSED)


def test_a_collection_file_is_not_a_run_report():
    collection = b'{"info": {"_postman_id": "x", "name": "c"}, "item": []}'
    assert registry.sniff(collection)["newman"] == 0.0
    with pytest.raises(ReportError, match="run.executions"):
        list(registry.parse(io.BytesIO(b'{"collection": {}}'), format="newman"))
    with pytest.raises(ReportError):
        list(registry.parse(io.BytesIO(b'{"collection": '), format="newman"))


def test_the_importer_is_a_registered_entry_point():
    assert "newman" in {ep.name for ep in entry_points(group="testkeel.importers")}
