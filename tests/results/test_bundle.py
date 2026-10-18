"""Run bundles: lossless export/import, schema rejection with line numbers, idempotency."""

from __future__ import annotations

import json

import pytest

from results.bundle import (
    RECORD_TYPES,
    BundleError,
    import_bundle,
    load_schema,
    read_bundle,
    write_bundle,
)
from results.store import open_store
from tests.results._fixtures import populate


def _lines(path):
    return path.read_text().splitlines()


def test_export_then_import_into_a_fresh_db_is_lossless(store, tmp_path):
    populate(store)
    first = write_bundle(store, "r1", tmp_path / "out")
    assert first.name == "bundle-r1.ndjson"
    types = [json.loads(line)["type"] for line in _lines(first)]
    assert types[0] == "run" and types[-1] == "run_end"
    assert set(types) == set(RECORD_TYPES)

    fresh = open_store(str(tmp_path / "fresh.db"))
    report = import_bundle(fresh, first)
    assert report.run_ids == ["r1"] and report.added["result"] == 4
    second = write_bundle(fresh, "r1", tmp_path / "again.ndjson")
    assert _lines(second) == _lines(first)
    fresh.close()


def test_importing_twice_changes_nothing(store, tmp_path):
    populate(store)
    path = write_bundle(store, "r1", tmp_path)
    fresh = open_store(str(tmp_path / "fresh.db"))
    import_bundle(fresh, path)
    again = import_bundle(fresh, path)
    assert all(again.added.get(t, 0) == 0 for t in ("result", "evidence", "verdict", "run"))
    fresh.close()


def test_gzip_bundles_round_trip(store, tmp_path):
    populate(store)
    path = write_bundle(store, "r1", tmp_path / "b.ndjson.gz")
    assert path.read_bytes()[:2] == b"\x1f\x8b"
    assert [r.type for r in read_bundle(path)][0] == "run"


def test_a_bundle_breaking_the_schema_is_rejected_with_its_line(store, tmp_path):
    bad = tmp_path / "bad.ndjson"
    bad.write_text(
        json.dumps({"type": "run", "schema_version": 1, "run_id": "x"})
        + "\n"
        + json.dumps(
            {
                "type": "result",
                "schema_version": 1,
                "run_id": "x",
                "nodeid": "t",
                "outcome": "flaky",
            }
        )
        + "\n"
    )
    with pytest.raises(BundleError) as err:
        import_bundle(store, bad)
    assert err.value.line == 2 and "outcome" in str(err.value)
    # Nothing was written: validation runs before the import.
    assert store.query("run_get", run_id="x") == []


def test_unreadable_lines_and_unknown_types_are_rejected(tmp_path):
    p = tmp_path / "b.ndjson"
    p.write_text("{not json\n")
    with pytest.raises(BundleError, match="line 1"):
        list(read_bundle(p))
    p.write_text(json.dumps({"type": "mystery", "schema_version": 1}) + "\n")
    with pytest.raises(BundleError):
        list(read_bundle(p))


def test_results_for_a_run_nobody_knows_are_refused(store, tmp_path):
    p = tmp_path / "orphan.ndjson"
    p.write_text(
        json.dumps(
            {
                "type": "result",
                "schema_version": 1,
                "run_id": "ghost",
                "nodeid": "t",
                "outcome": "passed",
            }
        )
        + "\n"
    )
    with pytest.raises(BundleError, match="ghost"):
        import_bundle(store, p)


def test_an_external_framework_bundle_imports(store, tmp_path):
    """What a non-Python framework sends: run, results with free-string ids, run_end."""
    p = tmp_path / "jest.ndjson"
    records = [
        {
            "type": "run",
            "schema_version": 1,
            "run_id": "js-1",
            "source": "bundle",
            "framework": "jest",
        },
        {
            "type": "result",
            "schema_version": 1,
            "run_id": "js-1",
            "nodeid": "login > rejects bad password",
            "outcome": "passed",
            "duration_ms": 5,
        },
        {"type": "run_end", "schema_version": 1, "run_id": "js-1", "status": "finished"},
    ]
    p.write_text("".join(json.dumps(r) + "\n" for r in records))
    import_bundle(store, p)
    assert store.run("js-1").framework == "jest"
    assert store.query("run_final_results", run_id="js-1")[0]["outcome"] == "passed"


def test_the_schema_is_valid_draft_2020_12():
    from jsonschema import Draft202012Validator

    Draft202012Validator.check_schema(load_schema())
