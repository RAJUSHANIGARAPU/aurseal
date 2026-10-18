"""python -m results: migrate, status, ingest, export, import, prune."""

from __future__ import annotations

import argparse
import json

import pytest

from results.cli import main, parse_age
from results.store import open_store
from tests.results._fixtures import populate


def test_migrate_and_status(db_path, capsys):
    assert main(["--db", str(db_path), "db", "migrate"]) == 0
    assert "applied core/0001_init.sql" in capsys.readouterr().out
    assert main(["--db", str(db_path), "db", "status"]) == 0
    assert "applied" in capsys.readouterr().out


def test_export_and_import_bundle(db_path, tmp_path, capsys):
    s = open_store(str(db_path))
    populate(s)
    s.close()
    out = tmp_path / "out"
    assert main(["export", "bundle", "--run-id", "r1", "--out", str(out)]) == 0
    bundle = out / "bundle-r1.ndjson"
    assert bundle.exists()
    other = tmp_path / "other.db"
    assert main(["--db", str(other), "import", "bundle", str(bundle)]) == 0
    report = json.loads(capsys.readouterr().out.split("\n", 1)[1])
    assert report["run_ids"] == ["r1"]


def test_export_of_an_unknown_run_says_so(db_path, tmp_path, capsys):
    assert main(["export", "bundle", "--run-id", "nope", "--out", str(tmp_path)]) == 2


def test_import_of_a_bad_bundle_fails(db_path, tmp_path):
    bad = tmp_path / "bad.ndjson"
    bad.write_text("{}\n")
    assert main(["import", "bundle", str(bad)]) == 1


def test_ingest_and_prune(db_path, tmp_path, capsys):
    s = open_store(str(db_path))
    populate(s)
    s.close()
    verdicts = tmp_path / "v.jsonl"
    verdicts.write_text(
        json.dumps(
            {
                "run_id": "r1",
                "nodeid": "t::z",
                "kind": "coverage",
                "verdict": "recorded",
                "timestamp": "2026-01-02T00:00:00Z",
            }
        )
        + "\n"
    )
    assert main(["ingest", "--run-id", "r1", "--verdicts", str(verdicts)]) == 0
    assert "ingested 1 new verdict" in capsys.readouterr().out
    assert main(["prune", "--keep-runs", "0"]) == 0
    assert "removed 1 run" in capsys.readouterr().out


def test_parse_age():
    assert parse_age("180d") < parse_age("1h")
    with pytest.raises(argparse.ArgumentTypeError):
        parse_age("soon")
