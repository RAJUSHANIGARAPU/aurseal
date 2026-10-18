"""The wheel-contents check shared by package.yml and publish.yml (tools.wheel_check)."""

from __future__ import annotations

import zipfile
from pathlib import Path

from tools.wheel_check import check, main

CLEAN = [
    "testkeel/__init__.py",
    "testkeel/templates/.gitignore.jinja",
    "testkeel-0.1.0.dist-info/METADATA",
]


def _wheel(directory: Path, names: list[str], version: str = "0.1.0") -> Path:
    path = directory / f"testkeel-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, "")
    return path


def test_a_wheel_of_only_the_package_is_clean(tmp_path, capsys):
    _wheel(tmp_path, CLEAN)
    assert check(tmp_path) == []
    assert main([str(tmp_path)]) == 0
    assert "only the package" in capsys.readouterr().out


def test_a_file_outside_the_package_is_named(tmp_path, capsys):
    _wheel(tmp_path, [*CLEAN, "gate/workflow_lint.py", "conftest.py"])
    [problem] = check(tmp_path)
    assert "gate/workflow_lint.py" in problem and "conftest.py" in problem
    assert main([str(tmp_path)]) == 1
    assert "gate/workflow_lint.py" in capsys.readouterr().err


def test_no_wheel_or_two_wheels_is_a_failure_not_a_pass(tmp_path):
    assert check(tmp_path) == [f"expected one testkeel wheel in {tmp_path}, found 0"]
    _wheel(tmp_path, CLEAN)
    _wheel(tmp_path, CLEAN, version="0.2.0")
    assert "found 2" in check(tmp_path)[0]
