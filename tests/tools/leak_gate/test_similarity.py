"""Code-similarity gate on synthetic sources only."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.leak_gate import fingerprint as fp
from tools.leak_gate import gitsrc, similarity

# Twenty distinct, word-rich lines: a "private" JavaScript module.
PRIVATE_JS = "\n".join(
    ["/** Settles an order ledger line by line. */", "export class LedgerSettler {"]
    + [
        f'  settleStep{i}(ledger) {{ return ledger.amountFor("bucket{i}")'
        f" * this.rateTable.factor{i}(); }}"
        for i in range(20)
    ]
    + ["}"]
)

# The same twenty lines ported to Python: other syntax, keywords and identifier case.
PORTED_PY = "\n".join(
    ['"""Settles an order ledger line by line."""', "class LedgerSettler:"]
    + [
        f"    def settle_step{i}(self, ledger): return ledger.amount_for('bucket{i}')"
        f" * self.rate_table.factor{i}()"
        for i in range(20)
    ]
)

UNRELATED = "\n".join(
    f"def handler_{i}(request): return render(request, 'page_{i}.html', {{'n': {i}}})"
    for i in range(30)
)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    src = tmp_path / "private"
    src.mkdir()
    (src / "ledger_settler.js").write_text(PRIVATE_JS, encoding="utf-8")
    (src / "image.bin").write_bytes(b"\0\1\2")
    out = tmp_path / "fp" / "fingerprints.bin"
    monkeypatch.setenv(similarity.ENV_VAR, str(out))
    assert similarity.main(["build", str(src)]) == 0
    return out


# --- tokenizer and winnowing ------------------------------------------------


def test_tokenizer_folds_case_and_separators_and_drops_punctuation() -> None:
    java = fp.tokenize("final int contractNumber = this.getX(null); // note")
    py = fp.tokenize("contract_number = self.get_x(None)  # note")
    assert [t.text for t in java] == [t.text for t in py] == ["contractnumber", "getx", "note"]


def test_tokens_keep_their_line() -> None:
    tokens = fp.tokenize("alpha\n\n  beta gamma\n")
    assert [(t.text, t.line) for t in tokens] == [("alpha", 1), ("beta", 3), ("gamma", 3)]


def test_winnowing_guarantee_holds() -> None:
    hashes = list(range(1000, 0, -7))
    kept = fp.winnow(hashes, 4)
    for i in range(len(hashes) - 3):
        assert kept & set(hashes[i : i + 4]), "every window keeps a fingerprint"


def test_winnow_short_inputs() -> None:
    assert fp.winnow([], 4) == set()
    assert fp.winnow([5, 3], 4) == {3}


def test_runs_tolerate_small_gaps_and_ignore_non_code_lines() -> None:
    code = set(range(1, 30)) - {5}  # line 5 is blank
    matched = set(range(1, 13)) - {7, 8}  # 2-line edit inside the copy
    assert fp.runs(matched, code, min_lines=9, max_gap=2) == [fp.Run(1, 12, 9)]
    assert fp.runs(matched, code, min_lines=9, max_gap=1) == []


# --- store -------------------------------------------------------------------


def test_store_holds_only_hashes(store: Path) -> None:
    blob = store.read_bytes()
    for word in (b"Ledger", b"ledger", b"settle", b"bucket", b"ledger_settler"):
        assert word not in blob
    assert blob.startswith(similarity.MAGIC)
    assert oct(store.stat().st_mode & 0o777) == "0o600"


def test_missing_store_fails_closed(tmp_path: Path, monkeypatch, repo, capsys) -> None:
    monkeypatch.setenv(similarity.ENV_VAR, str(tmp_path / "none.bin"))
    assert similarity.main(["check", "--tree", "--repo", str(repo.root)]) == 2
    assert "CANNOT RUN" in capsys.readouterr().err


def test_store_with_other_parameters_is_refused(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "fp.bin"
    monkeypatch.setattr(fp, "K", fp.K + 1)
    similarity.write_store(path, [{1, 2}])
    monkeypatch.undo()
    with pytest.raises(similarity.FingerprintsUnavailable, match="Rebuild"):
        similarity.read_store(path)


def test_truncated_or_foreign_store_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "fp.bin"
    similarity.write_store(path, [set(range(50))])
    path.write_bytes(path.read_bytes()[:-9])
    with pytest.raises(similarity.FingerprintsUnavailable, match="truncated"):
        similarity.read_store(path)
    path.write_bytes(b"not a store at all")
    with pytest.raises(similarity.FingerprintsUnavailable):
        similarity.read_store(path)


# --- check -------------------------------------------------------------------


def test_cross_language_port_is_flagged_with_repo_lines_only(store: Path, repo, capsys) -> None:
    repo.write("pkg/settler.py", "import math\n\n" + PORTED_PY + "\n")
    repo.write("pkg/views.py", UNRELATED + "\n")
    assert similarity.main(["check", "--tree", "--repo", str(repo.root)]) == 1
    out = capsys.readouterr().out
    assert "pkg/settler.py:" in out and "(private file #1)" in out
    assert "pkg/views.py" not in out
    assert "bucket" not in out and "Ledger" not in out


def test_short_copies_pass(store: Path, repo) -> None:
    eight_lines = "\n".join(PORTED_PY.splitlines()[2:10])
    repo.write("small.py", eight_lines + "\n")
    assert similarity.main(["check", "--tree", "--repo", str(repo.root)]) == 0


def test_staged_mode_fails_only_runs_touching_added_lines(store: Path, repo) -> None:
    repo.write("old.py", PORTED_PY + "\n")
    repo.commit("existing copy")  # history is the range check's job
    repo.write("new.py", "x = 1\n")
    repo.git("add", "-A")
    assert similarity.main(["check", "--staged", "--repo", str(repo.root)]) == 0
    repo.write("old.py", PORTED_PY + "\n# touched\nx = 2\n")
    repo.write("copy.py", PORTED_PY + "\n")
    repo.git("add", "-A")
    findings = [
        f
        for scan in gitsrc.staged(repo.root)
        for f in similarity.check_file(scan, similarity.read_store(store))
    ]
    assert [f.path for f in findings] == ["copy.py"]


def test_range_mode(store: Path, repo) -> None:
    base = repo.git("rev-parse", "HEAD").strip()
    repo.write("copy.py", PORTED_PY + "\n")
    repo.commit("add")
    assert similarity.main(["check", "--range", f"{base}..HEAD", "--repo", str(repo.root)]) == 1


def test_paths_filter(store: Path, repo) -> None:
    repo.write("copy.py", PORTED_PY + "\n")
    repo.write("ok.py", UNRELATED + "\n")
    args = ["check", "--tree", "--repo", str(repo.root), "--paths"]
    assert similarity.main([*args, "ok.py"]) == 0
    assert similarity.main([*args, "copy.py"]) == 1
