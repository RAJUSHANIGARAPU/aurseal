"""The v1 result model: round-trips, invariants and the exported JSON Schema."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from aurseal.model.identity import fingerprint
from aurseal.model.v1 import (
    SCHEMA_VERSION,
    Attempt,
    BuildContext,
    RunUpload,
    Status,
    TestRecord,
    export_json_schema,
    json_schema,
)

FP = fingerprint("pytest", "tests/test_cart.py", "TestCart", "test_total")


def _record(**overrides) -> TestRecord:
    fields = {
        "framework": "pytest",
        "path": "tests/test_cart.py",
        "suite": "TestCart",
        "name": "test_total",
        "fingerprint": FP,
        "status": Status.PASSED,
    }
    fields.update(overrides)
    return TestRecord(**fields)


def test_the_status_enum_is_the_result_status_model():
    assert [s.value for s in Status] == [
        "passed",
        "failed",
        "broken",
        "skipped",
        "blocked",
        "retest",
    ]
    assert "untested" not in {s.value for s in Status}
    assert {s for s in Status if s.is_failure} == {Status.FAILED, Status.BROKEN}


def test_an_upload_round_trips_through_json():
    upload = RunUpload(
        project="ACME",
        name="nightly",
        build=BuildContext(provider="github", commit="a" * 40, branch="main"),
        records=(
            _record(
                variant="EUR-10",
                configuration="chromium",
                status=Status.PASSED,
                flaky_in_run=True,
                attempts=(
                    Attempt(attempt_no=0, status=Status.FAILED, duration_ms=10),
                    Attempt(attempt_no=1, status=Status.PASSED, duration_ms=12),
                ),
            ),
        ),
    )
    again = RunUpload.model_validate_json(upload.model_dump_json())
    assert again == upload
    assert again.schema_version == SCHEMA_VERSION


def test_a_future_major_schema_version_is_rejected():
    with pytest.raises(ValidationError):
        RunUpload(schema_version="2.0")
    assert RunUpload(schema_version="1.7").schema_version == "1.7"


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        RunUpload.model_validate({"records": [], "surprise": 1})


def test_status_must_be_the_last_attempt():
    with pytest.raises(ValidationError, match="last attempt"):
        _record(
            status=Status.FAILED,
            attempts=(Attempt(attempt_no=0, status=Status.PASSED),),
        )


@pytest.mark.parametrize(
    ("statuses", "flaky"),
    [
        ((Status.FAILED, Status.PASSED), True),
        ((Status.BROKEN, Status.PASSED), True),
        ((Status.PASSED,), False),
        ((Status.PASSED, Status.FAILED), False),
    ],
)
def test_flaky_in_run_is_derived_from_the_attempts(statuses, flaky):
    attempts = tuple(Attempt(attempt_no=i, status=s) for i, s in enumerate(statuses))
    _record(status=statuses[-1], attempts=attempts, flaky_in_run=flaky)
    with pytest.raises(ValidationError, match="flaky_in_run"):
        _record(status=statuses[-1], attempts=attempts, flaky_in_run=not flaky)


def test_a_malformed_fingerprint_is_rejected():
    with pytest.raises(ValidationError):
        _record(fingerprint="not-a-fingerprint")


def test_the_counts_cover_every_status():
    upload = RunUpload(records=(_record(), _record(status=Status.FAILED, name="test_tax")))
    counts = upload.counts()
    assert counts[Status.PASSED] == 1 and counts[Status.FAILED] == 1
    assert set(counts) == set(Status)


def test_the_json_schema_describes_the_upload(tmp_path):
    schema = json_schema()
    assert schema["title"] == "RunUpload"
    assert {"schema_version", "records", "build"} <= set(schema["properties"])
    assert schema["$defs"]["Status"]["enum"] == [s.value for s in Status]
    path = export_json_schema(tmp_path / "run-upload.json")
    assert json.loads(path.read_text("utf-8")) == schema
    # Stable: two exports are byte-identical.
    assert export_json_schema(tmp_path / "again.json").read_bytes() == path.read_bytes()
