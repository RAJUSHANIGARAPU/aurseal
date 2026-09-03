"""The scanner two tier-3 plugins share, pinned on its own.

Both plugins default ``source_dir`` to ``"."``, and the unbounded ``rglob`` they
used to run from there walked the virtualenv: 3573 files in this repo, 3353 of
them ``site-packages``, roughly 26 seconds — inside a base class that retries
three times with no timeout. The plugin tests prove the dependency tree stops
appearing in their findings; this proves the pruning and the ceiling that get
them there, including the truncation flag neither plugin's own tests can reach.
"""

from __future__ import annotations

from plugins.tier3._source_scan import iter_source_files
from tests.plugins._tier3_support import write_source_tree


def test_project_files_are_found(tmp_path):
    root = write_source_tree(tmp_path, {"pkg/__init__.py": "", "pkg/mod.py": "x = 1\n"})
    assert [p.name for p in iter_source_files(root).files] == ["__init__.py", "mod.py"]


def test_environments_and_caches_are_pruned(tmp_path):
    root = write_source_tree(
        tmp_path,
        {
            "mine.py": "",
            ".venv/lib/python3.12/site-packages/dep.py": "",
            ".venv-py313/lib/site-packages/other.py": "",
            "venv/lib/dep.py": "",
            "node_modules/thing/setup.py": "",
            "__pycache__/stale.py": "",
            ".git/hooks/hook.py": "",
            "build/lib/copy.py": "",
            "thing.egg-info/meta.py": "",
            "ai_generated_tests/property/test_prop_mine.py": "",
        },
    )
    assert [p.name for p in iter_source_files(root).files] == ["mine.py"]


def test_non_python_files_are_ignored(tmp_path):
    root = write_source_tree(tmp_path, {"mine.py": "", "notes.md": "", "data.json": ""})
    assert [p.name for p in iter_source_files(root).files] == ["mine.py"]


def test_a_missing_directory_yields_nothing_rather_than_raising(tmp_path):
    """The caller decides what an empty tree means. For tier 3 that is
    ``unknown`` — but the scanner must not turn it into an ``error`` first."""
    scan = iter_source_files(tmp_path / "does-not-exist")
    assert scan.files == []
    assert scan.truncated is False


def test_the_ceiling_is_reported_not_silent(tmp_path):
    root = write_source_tree(tmp_path, {f"mod{n}.py": "" for n in range(10)})
    scan = iter_source_files(root, limit=4)
    assert len(scan.files) == 4
    assert scan.truncated is True


def test_a_complete_scan_says_so(tmp_path):
    """Control: `truncated` that was always True would satisfy the test above."""
    root = write_source_tree(tmp_path, {f"mod{n}.py": "" for n in range(3)})
    assert iter_source_files(root, limit=4).truncated is False


def test_results_are_deterministic(tmp_path):
    """The cap only makes sense against a stable order — otherwise which files
    a run looked at changes between runs on the same tree."""
    root = write_source_tree(tmp_path, {f"pkg/mod{n}.py": "" for n in range(20)})
    assert iter_source_files(root, limit=5).files == iter_source_files(root, limit=5).files
