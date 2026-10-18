"""The built wheel carries the template package data, dotfiles included."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "src" / "testkeel" / "templates"


@pytest.mark.skipif(not (TEMPLATES / "copier.yml").is_file(), reason="no templates in this tree")
def test_the_wheel_contains_every_template_file(tmp_path):
    builders = pytest.importorskip("hatchling.builders.wheel")
    builder = builders.WheelBuilder(str(ROOT))
    wheel = Path(next(iter(builder.build(directory=str(tmp_path), versions=["standard"]))))
    names = set(zipfile.ZipFile(wheel).namelist())
    expected = {
        "testkeel/templates/" + path.relative_to(TEMPLATES).as_posix()
        for path in TEMPLATES.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    assert any(Path(name).name.startswith(".") for name in expected), "no dotfile to check"
    assert expected - names == set()
    assert all(n.startswith(("testkeel/", "testkeel-")) for n in names)
