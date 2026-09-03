"""
The walk the tier-4 plugins share, and the thing it refuses to hide.

The plugins used ``rglob("*.py")`` from a ``source_dir`` defaulting to ``"."``,
which in this checkout reaches 3573 files, 3353 of them under ``.venv``. Pruning
is the obvious half. The half worth a test of its own is ``truncated``: a scan
that stopped early has not seen the tree it is about to report on, and it has to
say so rather than let a caller pass off a sample as the whole.
"""

from __future__ import annotations

from plugins.tier4._source_scan import relative_key, scan_source_files


def _tree(root):
    (root / "pkg").mkdir()
    (root / "pkg" / "mod.py").write_text("x = 1\n")
    (root / "pkg" / "test_mod.py").write_text("x = 1\n")
    for hidden in (".venv/lib/site-packages", "__pycache__", "node_modules", "reports"):
        path = root / hidden
        path.mkdir(parents=True)
        (path / "junk.py").write_text("x = 1\n")
    return root


class TestItWalksSourceAndNotDependencies:
    def test_dependency_and_cache_trees_are_pruned(self, tmp_path):
        scan = scan_source_files(_tree(tmp_path))

        assert [relative_key(f, tmp_path) for f in scan.files] == ["pkg/mod.py"]

    def test_tests_can_be_kept(self, tmp_path):
        """Positive control: the pruning is by directory, not by emptying the walk."""
        scan = scan_source_files(_tree(tmp_path), skip_tests=False)

        assert [relative_key(f, tmp_path) for f in scan.files] == [
            "pkg/mod.py",
            "pkg/test_mod.py",
        ]


class TestAPartialWalkSaysSo:
    def test_hitting_the_limit_sets_truncated(self, tmp_path):
        for i in range(5):
            (tmp_path / f"mod{i}.py").write_text("x = 1\n")

        scan = scan_source_files(tmp_path, limit=3)

        assert scan.count == 3
        assert scan.truncated is True

    def test_a_complete_walk_does_not(self, tmp_path):
        for i in range(3):
            (tmp_path / f"mod{i}.py").write_text("x = 1\n")

        scan = scan_source_files(tmp_path, limit=3)

        assert scan.truncated is False
