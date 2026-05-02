"""Test meta-quality plugin — scans tests for dead assertions and missing negatives."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult
from plugins.cost_governor import CostGovernor

_REPORT_DIR = Path("reports/test_quality")

_NEGATIVE_KEYWORDS = ("fail", "error", "invalid", "negative", "bad", "wrong", "reject")


class _AssertionVisitor(ast.NodeVisitor):
    """Finds dead assertions in test code."""

    def __init__(self) -> None:
        self.dead_assertions: int = 0

    def visit_Assert(self, node: ast.Assert) -> None:  # noqa: N802
        test = node.test
        # assert True
        if isinstance(test, ast.Constant) and test.value is True:
            self.dead_assertions += 1
        # assert x == x (same names)
        elif (
            isinstance(test, ast.Compare)
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)
            and len(test.comparators) == 1
            and isinstance(test.left, ast.Name)
            and isinstance(test.comparators[0], ast.Name)
            and test.left.id == test.comparators[0].id
        ):
            self.dead_assertions += 1
        self.generic_visit(node)


def _score_file(py_file: Path) -> dict:
    """Compute quality score for a test file."""
    try:
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except Exception:  # noqa: BLE001
        return {"file": str(py_file), "score": 0, "error": "parse failed"}

    visitor = _AssertionVisitor()
    visitor.visit(tree)

    test_names = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    ]
    has_negatives = any(any(kw in name.lower() for kw in _NEGATIVE_KEYWORDS) for name in test_names)

    score = 100
    score -= visitor.dead_assertions * 10
    if test_names and not has_negatives:
        score -= 20
    score = max(0, score)

    return {
        "file": str(py_file),
        "score": score,
        "dead_assertions": visitor.dead_assertions,
        "has_negative_tests": has_negatives,
        "test_count": len(test_names),
    }


class TestMetaQualityPlugin(BasePlugin):
    name = "test-meta-quality"
    priority = PluginPriority.NORMAL
    trigger_conditions = ["test_change", "manual"]

    def run(self, context: dict) -> PluginResult:
        tests_dir = Path(context.get("tests_dir", "tests"))
        governor = context.get("cost_governor") or CostGovernor()  # noqa: F841
        is_dry = context.get("dry_run", False)

        test_files = list(tests_dir.rglob("test_*.py")) if tests_dir.exists() else []
        findings: list[dict] = [_score_file(f) for f in test_files]

        if is_dry:
            return PluginResult(
                status="pass",
                findings=[{"files_scanned": len(findings), "scores": findings}],
                dry_run=True,
            )

        _REPORT_DIR.mkdir(parents=True, exist_ok=True)
        report_file = _REPORT_DIR / "test_quality_report.json"
        import json

        report_file.write_text(json.dumps(findings, indent=2), encoding="utf-8")

        avg_score = sum(f.get("score", 0) for f in findings) / max(len(findings), 1)
        status = "pass" if avg_score >= 70 else "warn"

        return PluginResult(
            status=status,
            findings=[
                {
                    "files_scanned": len(findings),
                    "average_score": round(avg_score, 1),
                    "report": str(report_file),
                    "scores": findings,
                }
            ],
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    ctx: dict = {"tests_dir": "tests"}
    plugin = TestMetaQualityPlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in ("pass", "skip", "warn") else 1)
