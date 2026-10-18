"""The visual engine offline: pixel comparison, baselines, snapshot outcomes, approval CLI.

Needs Pillow (requirements-checks.txt) to draw test images; skips cleanly without it.
"""

from __future__ import annotations

import io
import json

import pytest

from checks._sink import MemorySink
from checks.visual.baselines import BaselineStore, check_name, sha256
from checks.visual.compare import compare
from checks.visual.runner import combine
from checks.visual.snapshot import check_snapshot
from tests.checks._require import require_module


@pytest.fixture(autouse=True)
def pil():
    return require_module("PIL.Image")


def png(width=40, height=30, color=(255, 255, 255), dots=(), mode="RGB") -> bytes:
    from PIL import Image

    img = Image.new(mode, (width, height), color if mode == "RGB" else (*color, 255))
    for (x, y), c in dots:
        img.putpixel((x, y), c if mode == "RGB" else (*c, 255))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


class TestCompare:
    def test_identical_images_have_no_difference(self):
        diff = compare(png(), png())
        assert diff.outcome == "match" and diff.diff_pixels == 0 and diff.diff_png is None

    def test_one_changed_pixel_is_counted(self):
        diff = compare(png(), png(dots=[((3, 4), (0, 0, 0))]), max_diff_ratio=0)
        assert diff.outcome == "mismatch" and diff.diff_pixels == 1
        assert diff.diff_ratio == pytest.approx(1 / 1200)

    def test_a_change_within_the_ratio_still_matches(self):
        diff = compare(png(), png(dots=[((3, 4), (0, 0, 0))]), max_diff_ratio=0.01)
        assert diff.outcome == "match" and diff.diff_pixels == 1

    def test_noise_below_the_pixel_threshold_is_ignored(self):
        """Anti-aliasing-level noise: a 3-level grey shift is far under threshold 0.1."""
        diff = compare(png(), png(dots=[((x, 0), (252, 252, 252)) for x in range(40)]))
        assert diff.diff_pixels == 0 and diff.outcome == "match"

    def test_the_same_noise_counts_with_a_zero_threshold(self):
        diff = compare(png(), png(dots=[((1, 1), (252, 252, 252))]), pixel_threshold=0)
        assert diff.diff_pixels == 1

    def test_size_change_is_its_own_outcome(self):
        diff = compare(png(40, 30), png(41, 30))
        assert diff.outcome == "size_changed" and "dimensions changed" in diff.detail

    def test_a_diff_image_marks_the_pixel_red(self):
        from PIL import Image

        diff = compare(png(), png(dots=[((5, 6), (0, 0, 255))]), max_diff_ratio=0)
        image = Image.open(io.BytesIO(diff.diff_png)).convert("RGB")
        assert image.getpixel((5, 6)) == (255, 0, 0)
        assert image.getpixel((0, 0)) != (255, 0, 0)

    def test_transparency_is_flattened_onto_white(self):
        assert compare(png(mode="RGBA"), png()).outcome == "match"

    @pytest.mark.parametrize("baseline", [None, b"", b"not a png"])
    def test_an_empty_or_garbage_baseline_is_missing(self, baseline):
        assert compare(baseline, png()).outcome == "missing_baseline"

    def test_an_empty_actual_is_an_error(self):
        with pytest.raises(ValueError):
            compare(png(), b"")

    def test_bad_thresholds(self):
        with pytest.raises(ValueError):
            compare(png(), png(), pixel_threshold=2)


class TestBaselines:
    def test_a_zero_byte_baseline_is_missing(self, tmp_path):
        store = BaselineStore(tmp_path, "chromium", "linux")
        store.path("home").parent.mkdir(parents=True)
        store.path("home").write_bytes(b"")
        assert store.load("home") is None

    def test_store_refuses_to_write_an_empty_baseline(self, tmp_path):
        with pytest.raises(ValueError):
            BaselineStore(tmp_path, "chromium", "linux").write("home", b"")

    def test_baselines_are_per_browser_and_platform(self, tmp_path):
        store = BaselineStore(tmp_path, "firefox", "darwin")
        assert store.path("home") == tmp_path / "firefox-darwin" / "home.png"

    @pytest.mark.parametrize("name", ["../x", "a/b", "", ".hidden"])
    def test_unsafe_names_are_refused(self, name):
        with pytest.raises(ValueError):
            check_name(name)

    def test_approve_copies_bytes_and_records_the_sha(self, tmp_path):
        actual = tmp_path / "home.actual.png"
        actual.write_bytes(png(dots=[((1, 1), (0, 0, 0))]))
        store = BaselineStore(tmp_path / "base", "chromium", "linux")
        digest = store.approve("home", actual, run_id="run-9")
        assert store.load("home") == actual.read_bytes()
        assert digest == sha256(actual.read_bytes())
        record = json.loads((tmp_path / "base" / "approvals.jsonl").read_text().splitlines()[0])
        assert record["sha256"] == digest and record["run_id"] == "run-9"


class TestSnapshot:
    def _store(self, tmp_path, baseline: bytes | None = None) -> BaselineStore:
        store = BaselineStore(tmp_path / "base", "chromium", "linux")
        if baseline is not None:
            store.write("home", baseline)
        return store

    def test_match_passes_and_writes_the_actual(self, tmp_path):
        out, _ = check_snapshot(png(), "home", self._store(tmp_path, png()), out_dir=tmp_path / "r")
        assert out.status == "pass"
        assert (tmp_path / "r" / "home.actual.png").is_file()

    def test_mismatch_fails_with_a_diff_image(self, tmp_path):
        sink = MemorySink()
        actual = png(dots=[((x, 2), (0, 0, 0)) for x in range(20)])
        out, _ = check_snapshot(
            actual, "home", self._store(tmp_path, png()), out_dir=tmp_path / "r", sink=sink
        )
        assert out.status == "fail" and "mismatch" in out.reason
        assert (tmp_path / "r" / "home.diff.png").is_file()
        row = sink.rows["metric_visual_diff"][0]
        assert row["outcome"] == "mismatch" and row["diff_pixels"] == 20

    def test_missing_baseline_is_unknown_not_pass(self, tmp_path, monkeypatch):
        monkeypatch.delenv("VISUAL_UPDATE", raising=False)
        out, _ = check_snapshot(png(), "home", self._store(tmp_path), out_dir=tmp_path / "r")
        assert out.status == "unknown" and "nothing compared" in out.reason

    def test_update_takes_the_baseline_and_is_skip(self, tmp_path):
        store = self._store(tmp_path)
        out, _ = check_snapshot(png(), "home", store, out_dir=tmp_path / "r", update=True)
        assert out.status == "skip" and out.baseline_taken
        assert store.load("home") == png()

    def test_update_never_overwrites_an_existing_baseline(self, tmp_path):
        store = self._store(tmp_path, png())
        out, _ = check_snapshot(
            png(color=(0, 0, 0)), "home", store, out_dir=tmp_path / "r", update=True
        )
        assert out.status == "fail"
        assert store.load("home") == png()

    def test_combine(self, tmp_path):
        store = self._store(tmp_path, png())
        ok, _ = check_snapshot(png(), "home", store, out_dir=tmp_path)
        bad, _ = check_snapshot(png(color=(0, 0, 0)), "home", store, out_dir=tmp_path)
        missing, _ = check_snapshot(png(), "other", store, out_dir=tmp_path, update=False)
        assert combine([ok]).status == "pass"
        assert combine([ok, bad, missing]).status == "fail"
        assert combine([ok, missing]).status == "unknown"
        assert combine([]).status == "unknown"


class TestCli:
    def test_compare_exit_codes(self, tmp_path, capsys):
        from checks.visual.__main__ import main

        a, b = tmp_path / "a.png", tmp_path / "b.png"
        a.write_bytes(png())
        b.write_bytes(png(color=(0, 0, 0)))
        assert main(["compare", str(a), str(a)]) == 0
        assert main(["compare", str(a), str(b), "--diff", str(tmp_path / "d.png")]) == 1
        assert (tmp_path / "d.png").is_file()
        assert main(["compare", str(tmp_path / "none.png"), str(a)]) == 2
        capsys.readouterr()

    def test_approve_from_a_run(self, tmp_path, monkeypatch):
        from checks.visual import __main__ as cli
        from checks.visual import baselines

        run_dir = tmp_path / "reports" / "run-5"
        run_dir.mkdir(parents=True)
        (run_dir / "home.actual.png").write_bytes(png())
        monkeypatch.setattr(cli, "report_dir", lambda run: tmp_path / "reports" / run)
        monkeypatch.setattr(baselines, "DEFAULT_ROOT", tmp_path / "base")
        assert cli.main(["approve", "--run", "run-5", "--platform", "linux"]) == 0
        assert (tmp_path / "base" / "chromium-linux" / "home.png").read_bytes() == png()
        assert cli.main(["approve", "--run", "run-5", "--name", "absent"]) == 2
