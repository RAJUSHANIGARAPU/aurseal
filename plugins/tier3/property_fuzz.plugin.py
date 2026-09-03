"""Property fuzz plugin — writes Hypothesis property tests for project functions.

Three separate things made the old count untrue.

The write happened inside the per-function loop but the filename was derived
from the *module*, so three functions in one file produced three writes to one
path: the last overwrote the other two while all three were appended to
``generated``. The plugin reported three generated tests and had one on disk.
Generation is now per module — every function that module contributes lands in
the same file, and the file is written once.

Model output was spliced into Python source on the strength of ``"st." in hint``.
A perfectly reasonable reply — "Use st.integers() for x." — satisfies that and
produces a file that is a ``SyntaxError``, written to disk and counted as a
generated test. The hint is now parsed and structurally checked before it is
used, and the assembled module is ``ast.parse``d before it is written: a file
that does not parse is not a generated test, so it is not written and the run
does not claim it.

``module_import`` was built from the absolute path, giving dotted names like
``.Users.someone.repo.core.thing`` that no interpreter can import. Every
generated test therefore fell straight into its own ``except`` and passed
having called nothing. The path is now relative to the source root, and the
generated module fronts it with ``pytest.importorskip`` so an unimportable
target reports as a skip in the test run rather than as a green.

Like ``chaos-resilience``, this plugin cannot return ``fail``. It writes tests;
it does not run them. Whether the product is well is the generated suite's
verdict to give, not this plugin's.
"""

from __future__ import annotations

import ast
import keyword
import logging
import sys
from pathlib import Path

from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult, PluginStatus
from plugins.cost_governor import CostGovernor
from plugins.tier3._source_scan import iter_source_files

logger = logging.getLogger(__name__)

#: Bounds on how much a single run generates. Both were implicit before.
_MAX_MODULES = 10
_MAX_FUNCTIONS_PER_MODULE = 3

#: Node types a strategies fragment from the model is allowed to contain. Any
#: other node — an attribute assignment, a lambda, a comprehension, a name that
#: is not ``st`` — and the fragment is discarded in favour of the defaults.
_ALLOWED_HINT_NODES = (
    ast.Call,
    ast.Attribute,
    ast.Name,
    ast.Constant,
    ast.keyword,
    ast.Load,
    ast.Tuple,
    ast.List,
    ast.UnaryOp,
    ast.USub,
    ast.UAdd,
)


class _FunctionVisitor(ast.NodeVisitor):
    """Collects top-level function definitions that are worth fuzzing."""

    def __init__(self) -> None:
        self.functions: list[tuple[str, list[str]]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        if node.name.startswith("_"):
            return
        args = [a.arg for a in node.args.args if a.arg != "self"]
        # A zero-argument function has nothing to draw values for, and
        # `@given()` with no strategies is a runtime error in Hypothesis that
        # `ast.parse` cannot see. Filtering here is the only place it is cheap.
        if args:
            self.functions.append((node.name, args))

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self.visit_FunctionDef(node)  # type: ignore[arg-type]


_PROP_TEMPLATE = '''"""Auto-generated Hypothesis property tests for {module}."""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# The target may not be importable from wherever this suite is run. A skip says
# so; swallowing the import failure inside each test would say "passed".
pytest.importorskip("{module_import}")

from {module_import} import {names}  # noqa: E402

{tests}
'''

_TEST_TEMPLATE = '''@given({strategies})
@settings(max_examples=50)
def test_{func_name}_properties({args}) -> None:
    """Property test for {func_name} — should not raise."""
    try:
        {func_name}({call_args})
    except (TypeError, ValueError, AttributeError):
        pass  # expected for boundary inputs
'''


def _dotted_module(py_file: Path, root: Path) -> str:
    """Dotted import path for ``py_file`` relative to ``root``, or "" if there is none.

    The old code stringified the absolute path, which yields a dotted name
    starting with the filesystem root and importable from nowhere.
    """
    try:
        relative = py_file.resolve().relative_to(root)
    except ValueError:
        return ""
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    if not parts:
        return ""
    if any(not part.isidentifier() or keyword.iskeyword(part) for part in parts):
        return ""
    return ".".join(parts)


def _validated_strategies(hint: str, args: list[str]) -> str:
    """Return the model's strategies fragment only if it is safely usable.

    "Usable" is checked structurally, not by substring: the fragment has to
    parse as a call's keyword arguments, name exactly this function's
    parameters, and consist only of ``st.…`` expressions. Prose that mentions
    ``st.`` does not survive any of those.
    """
    fragment = (hint or "").strip().rstrip(",")
    if not fragment:
        return ""
    try:
        parsed = ast.parse(f"_({fragment})", mode="eval")
    except (SyntaxError, ValueError):
        return ""
    call = parsed.body
    if not isinstance(call, ast.Call) or call.args or not call.keywords:
        return ""
    named = [kw.arg for kw in call.keywords]
    if any(name is None for name in named) or set(named) != set(args):
        return ""
    for kw in call.keywords:
        # Each value has to be a call, and everything inside it has to be a
        # plain `st.…` expression. `_(...)` is only the harness that made the
        # fragment parseable, so it is not itself inspected.
        if not isinstance(kw.value, ast.Call):
            return ""
        for node in ast.walk(kw.value):
            if not isinstance(node, _ALLOWED_HINT_NODES):
                return ""
            if isinstance(node, ast.Name) and node.id != "st":
                return ""
    return fragment


def _default_strategies(args: list[str]) -> str:
    return ", ".join(f"{a}=st.one_of(st.text(), st.integers(), st.none())" for a in args)


class PropertyFuzzPlugin(BasePlugin):
    name = "property-fuzz"
    priority = PluginPriority.NORMAL
    trigger_conditions = ["source_change", "manual"]

    def _collect(self, source_dir: Path) -> tuple[list[tuple[Path, str, list]], list[dict]]:
        """Return (per-module targets, notes about modules that were passed over)."""
        root = source_dir.resolve()
        targets: list[tuple[Path, str, list]] = []
        notes: list[dict] = []
        for py_file in iter_source_files(source_dir).files:
            try:
                relative = str(py_file.resolve().relative_to(root))
            except (OSError, ValueError):
                continue
            # Matched against the path *relative* to the source root: the old
            # check ran over the absolute path, so a checkout living under any
            # directory called "plugins" excluded the entire project.
            if "test" in py_file.name or "plugin" in relative:
                continue
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, ValueError):
                continue
            visitor = _FunctionVisitor()
            visitor.visit(tree)
            functions = visitor.functions[:_MAX_FUNCTIONS_PER_MODULE]
            if not functions:
                continue
            dotted = _dotted_module(py_file, root)
            if not dotted:
                notes.append(
                    {"file": str(py_file), "reason": "no importable dotted path from source_dir"}
                )
                continue
            targets.append((py_file, dotted, functions))
            if len(targets) >= _MAX_MODULES:
                break
        return targets, notes

    def _strategies_for(
        self, governor: CostGovernor, func_name: str, args: list[str]
    ) -> tuple[str, int, str]:
        """Return (strategies fragment, approx tokens, rejection reason if any)."""
        prompt = (
            f"For function `{func_name}({', '.join(args)})`, suggest Hypothesis strategies "
            "for property-based testing. Return only the strategies= part, e.g. "
            "x=st.integers(min_value=0), y=st.text(max_size=100)"
        )
        try:
            from api.llm.claude_client import ClaudeLLMClient

            model = governor.get_model("claude-haiku-4-5-20251001")
            llm = ClaudeLLMClient(model=model)

            def _call(text: str) -> str:
                completion = llm.complete_result(text)
                return completion.text if completion.ok else ""

            hint = governor.cached_complete(prompt, _call)
        except Exception as exc:  # noqa: BLE001 — an unreachable model is not fatal here
            logger.debug("property-fuzz: hint call raised %s: %s", type(exc).__name__, exc)
            return _default_strategies(args), 0, ""

        tokens = len(hint) // 4 if hint else 0
        if not hint:
            return _default_strategies(args), tokens, ""
        validated = _validated_strategies(hint, args)
        if not validated:
            # Defaults still produce a real test, so this is a note rather than
            # a verdict — but it is recorded, because silently ignoring the
            # model while billing for it is its own kind of dishonesty.
            return _default_strategies(args), tokens, f"unusable strategies hint for {func_name}"
        return validated, tokens, ""

    def run(self, context: dict) -> PluginResult:
        source_dir = Path(context.get("source_dir", "."))
        governor = context.get("cost_governor") or CostGovernor()
        is_dry = bool(context.get("dry_run", False))
        out_dir = Path(context.get("out_dir") or "ai_generated_tests/property")

        targets, notes = self._collect(source_dir)
        targeted_functions = [
            {"file": str(py_file), "function": name, "args": args}
            for py_file, _dotted, functions in targets
            for name, args in functions
        ]

        if not targeted_functions:
            return PluginResult(
                status=PluginStatus.UNKNOWN.value,
                findings=[
                    {
                        "reason": "no fuzzable functions found — nothing was generated",
                        "source_dir": str(source_dir),
                        "notes": notes,
                    }
                ],
                dry_run=is_dry,
            )

        if is_dry:
            return PluginResult(
                status=PluginStatus.PASS.value,
                findings=[
                    {
                        "targeted_functions": targeted_functions,
                        "count": len(targeted_functions),
                        "notes": notes,
                    }
                ],
                dry_run=True,
            )

        out_dir.mkdir(parents=True, exist_ok=True)
        generated: list[str] = []
        unparseable: list[dict] = []
        total_tokens = 0

        for py_file, dotted, functions in targets:
            tests: list[str] = []
            for func_name, args in functions:
                strategies, tokens, note = self._strategies_for(governor, func_name, args)
                total_tokens += tokens
                if note:
                    notes.append({"file": str(py_file), "reason": note})
                call_args = ", ".join(args)
                tests.append(
                    _TEST_TEMPLATE.format(
                        func_name=func_name,
                        strategies=strategies,
                        args=call_args,
                        call_args=call_args,
                    )
                )

            module_content = _PROP_TEMPLATE.format(
                module=py_file.stem,
                module_import=dotted,
                names=", ".join(name for name, _args in functions),
                tests="\n\n".join(tests),
            )
            try:
                ast.parse(module_content)
            except SyntaxError as exc:
                # Never write it. A file that does not parse is not a test, and
                # on this path it would land on top of a previous run's working
                # one.
                unparseable.append(
                    {"file": str(py_file), "error": f"{exc.msg} (line {exc.lineno})"}
                )
                continue

            module_file = out_dir / f"test_prop_{py_file.stem}.py"
            module_file.write_text(module_content, encoding="utf-8")
            generated.append(str(module_file))

        findings = [
            {
                "generated_files": generated,
                "generated_count": len(generated),
                "targeted_functions": targeted_functions,
                "notes": notes,
            }
        ]
        if unparseable:
            findings.append(
                {
                    "reason": "generated source did not parse and was discarded",
                    "unparseable": unparseable,
                }
            )
            return PluginResult(
                status=PluginStatus.UNKNOWN.value, findings=findings, tokens_used=total_tokens
            )
        if not generated:
            findings.append({"reason": "no test module was written"})
            return PluginResult(
                status=PluginStatus.UNKNOWN.value, findings=findings, tokens_used=total_tokens
            )

        return PluginResult(
            status=PluginStatus.PASS.value, findings=findings, tokens_used=total_tokens
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--source-dir", default=".")
    args = parser.parse_args()
    ctx: dict = {"source_dir": args.source_dir}
    plugin = PropertyFuzzPlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in (PluginStatus.PASS.value, PluginStatus.WARN.value) else 1)
