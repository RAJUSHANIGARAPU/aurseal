"""The committed artefact samples (docs/artefact-samples/) and their reference (docs/artefacts.md).

Each sample is parsed with the reader its real consumer uses, so a format change fails here
as well as showing up as a sample diff.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
from pathlib import Path

import pytest

from autonomous_ui.flakiness.history_store import HistoryStore
from autonomous_ui.models import FailureBundle
from gate.evidence import junit_totals
from gate.integrity import check_marker, read_marker
from gate.merge import _parse_properties
from gate.verdicts import read_verdict_rows
from reporting.allure_categories import categorize
from reporting.artefact_samples import FIXED_ISO, differences, generate, main
from results.bundle import read_bundle
from results.io.junit import iter_testcases
from traceability.links import load_case_links

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "docs" / "artefact-samples"
DOC = (ROOT / "docs" / "artefacts.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def fresh(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("fresh") / "samples"
    generate(out)
    return out


# ------------------------------------------------------------------ parse like the consumer


def test_report_results_and_categories():
    results = [json.loads(p.read_text()) for p in (SAMPLES / "allure-results").glob("*.json")]
    assert sorted(r["status"] for r in results) == ["failed", "passed", "skipped", "skipped"]
    categories = json.loads((SAMPLES / "allure-categories.json").read_text())
    expected_red = next(r for r in results if r["name"] == "test_purpose_line_is_present")
    message = expected_red["statusDetails"]["message"]
    assert (
        categorize("skipped", message, categories) == "Known defect still reproduces (expected red)"
    )


def test_report_environment_and_executor():
    rows = _parse_properties((SAMPLES / "allure-environment.properties").read_text())
    assert rows["Run.Id"] == "sample-run" and rows["Base.URL"].startswith("http://127.0.0.1")
    assert json.loads((SAMPLES / "allure-executor.json").read_text())["type"] == "local"


def test_junit():
    totals = junit_totals("sample", [SAMPLES / "junit.xml"]).totals
    assert (totals.tests, totals.failures, totals.skipped) == (4, 1, 2)
    assert len(list(iter_testcases(SAMPLES / "junit.xml"))) == 4


def test_run_trust_markers(tmp_path):
    shutil.copy(SAMPLES / "run-trust.txt", tmp_path / "run-trust-sample.txt")
    shutil.copy(SAMPLES / "run-trust.excerpt.json", tmp_path / "run-trust-sample.json")
    assert check_marker(tmp_path, "sample") == 0
    assert read_marker(tmp_path, "sample")["verdict"] == "PASS"


def test_detector_verdicts():
    rows = read_verdict_rows(SAMPLES / "verdicts.jsonl")
    assert [(r.verdict, r.run_id) for r in rows] == [("reproduces", "sample-run")]
    assert "BUG-7" in (SAMPLES / "verdict-status.md").read_text()


def test_case_links():
    links = load_case_links(SAMPLES / "case-links.json").links
    assert [link.requirements for link in links] == [("REQ-1",)] or links[0].requirements


def test_failure_bundle():
    bundle = FailureBundle.from_dict(
        json.loads((SAMPLES / "failure-bundle.excerpt.json").read_text())
    )
    assert bundle.test == "test_a_failing_expectation"


def test_flakiness_history():
    records = HistoryStore(store_path=SAMPLES / "flakiness-history.excerpt.jsonl").load_all()
    assert {r.outcome for r in records} >= {"passed", "failed", "skipped"}


def test_results_bundle():
    kinds = {record.type for record in read_bundle(SAMPLES / "results-bundle.excerpt.ndjson")}
    assert {"run", "result", "verdict", "run_end"} <= kinds


# ------------------------------------------------------------------ regeneration


def test_check_passes_on_the_committed_samples(fresh):
    assert differences(SAMPLES, fresh) == []


def test_check_fails_when_a_sample_is_edited_by_hand(tmp_path, fresh):
    copy = tmp_path / "samples"
    shutil.copytree(SAMPLES, copy)
    with (copy / "junit.xml").open("a") as handle:
        handle.write("<!-- edited -->\n")
    assert differences(copy, fresh) == ["differs from a fresh run: junit.xml"]


def test_the_check_cli_on_an_edited_copy(tmp_path):
    copy = tmp_path / "samples"
    shutil.copytree(SAMPLES, copy)
    (copy / "verdict-status.md").write_text("edited\n")
    assert main(["--check", "--out", str(copy)]) == 1


# ------------------------------------------------------------------ normalisation


_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
_URL_HOST = re.compile(r"https?://([^/:\"'\s]+)")


def _sample_texts():
    return [(p, p.read_text(encoding="utf-8")) for p in sorted(SAMPLES.rglob("*")) if p.is_file()]


def test_samples_carry_no_home_path_host_or_real_timestamp():
    home = str(Path.home())
    for path, text in _sample_texts():
        assert home not in text, path
        assert all(h in ("127.0.0.1", "localhost") for h in _URL_HOST.findall(text)), path
        assert {m.group(0) for m in _ISO.finditer(text)} <= {FIXED_ISO}, path


# ------------------------------------------------------------------ the reference doc


def _index_rows() -> list[list[str]]:
    section = DOC.split("## Index", 1)[1].split("\n## ", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("| ") and "---" not in line]
    return [[cell.strip() for cell in row.strip("|").split("|")] for row in rows[1:]]


def test_every_row_has_a_sample_or_a_note_and_every_sample_a_row():
    named: set[str] = set()
    for row in _index_rows():
        sample = row[-1]
        if sample.startswith("no sample:"):
            continue
        name = sample.strip("`")
        assert (SAMPLES / name).exists(), f"{row[0]}: {name} missing"
        named.add(name.rstrip("/"))
    files = {
        p.relative_to(SAMPLES).parts[0] if p.parent != SAMPLES else p.name
        for p in SAMPLES.rglob("*")
        if p.is_file()
    }
    assert files == named


def _module_exists(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


def test_every_named_producer_and_consumer_module_exists():
    modules = set()
    for row in _index_rows():
        for cell in (row[1], row[5]):
            modules |= set(re.findall(r"`([a-z_]+(?:\.[a-z_]+)+)`", cell))
    assert modules, "the index names no module"
    missing = sorted(m for m in modules if not _module_exists(m))
    assert missing == []
