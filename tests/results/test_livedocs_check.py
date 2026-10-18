"""The CI guard: orphan feature markers and empty directives are reported."""

from __future__ import annotations

from pathlib import Path

import pytest

from results.catalogue import Catalogue, build_catalogue
from results.cli import main
from results.livedocs import check, parse_page
from results.models import CaseRecord
from tests.results._livedocs_fixture import write_docs

pytestmark = pytest.mark.feature("living-documentation")

ROOT = Path(__file__).resolve().parents[2]


def _cat(*cases):
    return Catalogue(cases=list(cases))


def test_clean_docs_have_no_issues(tmp_path):
    docs = write_docs(tmp_path)
    cat = _cat(CaseRecord("c-1", "t::a", links=(("feature", "billing"), ("requirement", "REQ-12"))))
    assert check(docs, cat) == []


def test_orphans_and_empty_directives(tmp_path):
    docs = write_docs(tmp_path)
    (docs / "broken.md").write_text("---\nfeature: [x\n---\n# Broken\n")
    cat = _cat(CaseRecord("c-1", "t::a", links=(("feature", "search"),)))
    codes = sorted(i.code for i in check(docs, cat))
    assert codes == [
        "empty-directive",  # feature=billing has no case
        "empty-directive",  # requirement=REQ-12 has no case
        "malformed-front-matter",
        "orphan-feature",  # feature("search") is declared by no page
    ]
    orphan = next(i for i in check(docs, cat) if i.code == "orphan-feature")
    assert "search" in orphan.message and "t::a" in orphan.message


def test_cli_check_uses_a_catalogue_file(tmp_path, capsys):
    import json

    docs = write_docs(tmp_path)
    cat = _cat(CaseRecord("c-1", "t::a", links=(("feature", "billing"), ("requirement", "REQ-12"))))
    path = tmp_path / "catalogue.json"
    path.write_text(json.dumps(cat.to_json()))
    assert main(["livedocs", "--check", "--docs-dir", str(docs), "--catalogue", str(path)]) == 0
    assert "0 issue(s)" in capsys.readouterr().out
    (docs / "x.md").write_text("# X\n<!-- living:cases feature=ghost -->\n")
    assert main(["livedocs", "--check", "--docs-dir", str(docs), "--catalogue", str(path)]) == 1


def test_the_living_documentation_page_dogfoods_these_tests():
    page = parse_page(ROOT / "docs" / "features" / "living-documentation.md")
    assert "living-documentation" in page.features and not page.issues
    cat = build_catalogue(ROOT, ["tests/results/test_livedocs_check.py"])
    assert not cat.errors
    issues = [i for i in check(ROOT / "docs" / "features", cat) if i.code != "orphan-feature"]
    assert [i for i in issues if "living-documentation" in i.message] == []
