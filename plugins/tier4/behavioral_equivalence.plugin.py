"""
Public-surface drift plugin — diffs the public API across a refactor.

**Read the name of the finding, not the name of the file.** Nothing here is
executed. No function is called, no output is captured, no two runs are
compared. It parses the tree before a refactor and after it and diffs what it
can see statically. Calling that "behavioral equivalence" is the reason the old
version was believed: it reported "equivalent" about behaviour it had never
observed.

What it compared was a **flat, repo-wide set of function names**:

    prev_funcs = {f["function"] for f in prev.get("functions", [])}

Three consequences, all of them false greens.

- Change every line of every function body and the name set is identical, so
  the refactor reports no drift at all.
- Delete ``parse()`` from ``a.py`` while ``b.py`` also defines ``parse()`` and
  ``removed`` comes back empty — the file each name came from was collected at
  the line above and then discarded.
- ``visitor.functions[:5]`` capped each file at five public functions, so the
  sixth could appear or vanish unseen.

Now every function is keyed by ``(file, qualified name)``, its signature is
compared, and its body is hashed — so a changed body is drift, a moved function
is a removal and an addition, and the sixth function in a file is not invisible.
A changed signature is reported as ``fail`` without asking a model: a public
function whose parameters moved has broken its callers whatever the intent was.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import sys
from pathlib import Path

from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult, PluginStatus
from plugins.cost_governor import CostGovernor
from plugins.tier4._source_scan import relative_key, scan_source_files

logger = logging.getLogger(__name__)

_SNAPSHOT_DIR = Path("reports/behavioral_snapshots")

#: Bumped when the snapshot's shape changes. A snapshot written by the old
#: name-set version cannot be diffed by this one, and pretending otherwise
#: would report the entire codebase as removed and re-added.
_SNAPSHOT_VERSION = 2

#: Stated in every result, because the plugin's name promises more than it does.
_SCOPE = "static public-surface comparison; no function was executed"

#: How many names of each kind reach the prompt. The classification is one call.
_SAMPLE = 40


class _PublicFuncVisitor(ast.NodeVisitor):
    """Public functions, each named by the class it lives in."""

    def __init__(self) -> None:
        self.functions: list[dict] = []
        self._scope: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        if node.name.startswith("_"):
            return
        self.functions.append(
            {
                "qualname": ".".join([*self._scope, node.name]),
                "signature": _signature(node),
                "body_hash": _body_hash(node),
            }
        )
        # Deliberately not descending. A nested function is an implementation
        # detail of the one that encloses it, and the enclosing body hash
        # already covers every change made to it.

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self.visit_FunctionDef(node)  # type: ignore[arg-type]


def _signature(node: ast.FunctionDef) -> str:
    """The caller-visible shape of the parameter list.

    Names and order, plus how many arguments are required — losing a default is
    a break for every caller that relied on it, and adding one is not.
    """
    args = node.args
    names = [p.arg for p in (*args.posonlyargs, *args.args)]
    if args.vararg:
        names.append("*" + args.vararg.arg)
    names.extend(p.arg for p in args.kwonlyargs)
    if args.kwarg:
        names.append("**" + args.kwarg.arg)
    required = len(args.posonlyargs) + len(args.args) - len(args.defaults)
    return f"({', '.join(names)}) required={required}"


def _body_hash(node: ast.FunctionDef) -> str:
    """A hash of the body alone, so a signature change is not reported twice.

    ``ast.unparse`` normalises whitespace, comments and quoting away, which is
    what makes this a change detector rather than a formatting detector.
    """
    body = ast.unparse(ast.Module(body=list(node.body), type_ignores=[]))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def _collect(source_dir: Path) -> tuple[list[dict], bool]:
    """Every public function under ``source_dir``, and whether the walk was complete."""
    scan = scan_source_files(source_dir)
    functions: list[dict] = []
    for py_file in scan.files:
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        visitor = _PublicFuncVisitor()
        visitor.visit(tree)
        name = relative_key(py_file, source_dir)
        for func in visitor.functions:
            functions.append({"file": name, **func})
    return functions, scan.truncated


def _by_key(functions: list[dict]) -> dict[tuple[str, str], dict]:
    return {(f["file"], f["qualname"]): f for f in functions}


def _label(key: tuple[str, str]) -> str:
    return f"{key[0]}::{key[1]}"


class BehavioralEquivalencePlugin(BasePlugin):
    name = "behavioral-equivalence"
    priority = PluginPriority.HIGH
    trigger_conditions = ["pre_refactor", "post_refactor"]

    def run(self, context: dict) -> PluginResult:
        source_dir = Path(context.get("source_dir", "."))
        governor = context.get("cost_governor") or CostGovernor()
        trigger = context.get("trigger", "pre_refactor")
        is_dry = context.get("dry_run", False)

        public_funcs, truncated = _collect(source_dir)

        if is_dry:
            return PluginResult(
                status=PluginStatus.SKIP.value,
                findings=[
                    {
                        "functions_to_snapshot": public_funcs[:_SAMPLE],
                        "count": len(public_funcs),
                        "scope": _SCOPE,
                    }
                ],
                dry_run=True,
            )

        if truncated:
            return PluginResult(
                status=PluginStatus.UNKNOWN.value,
                findings=[
                    {
                        "reason": f"source scan stopped at {len(public_funcs)} functions; "
                        f"the tree under {source_dir} was not read in full",
                        "scope": _SCOPE,
                    }
                ],
            )

        _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        snapshot_file = _SNAPSHOT_DIR / "snapshot.json"

        if trigger == "pre_refactor":
            snapshot_file.write_text(
                json.dumps(
                    {
                        "version": _SNAPSHOT_VERSION,
                        "trigger": "pre_refactor",
                        "functions": public_funcs,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            # Recording a baseline is not a verdict about anything. The comparison
            # happens on the post_refactor run, and that is what should be scored.
            return PluginResult(
                status=PluginStatus.SKIP.value,
                findings=[
                    {
                        "snapshot_saved": str(snapshot_file),
                        "count": len(public_funcs),
                        "scope": _SCOPE,
                    }
                ],
            )

        if not snapshot_file.exists():
            # A missing baseline is a broken precondition, not an inapplicable
            # check: the run was asked to compare and could not.
            return PluginResult(
                status=PluginStatus.UNKNOWN.value,
                findings=[{"reason": "no pre_refactor snapshot found", "scope": _SCOPE}],
            )

        try:
            prev = json.loads(snapshot_file.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            return PluginResult(
                status=PluginStatus.UNKNOWN.value,
                findings=[{"reason": f"snapshot unreadable: {exc}", "scope": _SCOPE}],
            )

        if prev.get("version") != _SNAPSHOT_VERSION:
            return PluginResult(
                status=PluginStatus.UNKNOWN.value,
                findings=[
                    {
                        "reason": "snapshot was written in an older format and carries no "
                        "per-file keys, signatures or body hashes — re-run pre_refactor",
                        "scope": _SCOPE,
                    }
                ],
            )

        return self._compare(prev.get("functions", []), public_funcs, governor)

    def _compare(self, prev_funcs: list[dict], curr_funcs: list[dict], governor) -> PluginResult:
        prev = _by_key(prev_funcs)
        curr = _by_key(curr_funcs)

        added = sorted(curr.keys() - prev.keys())
        removed = sorted(prev.keys() - curr.keys())
        survived = prev.keys() & curr.keys()
        resigned = {k for k in survived if prev[k].get("signature") != curr[k].get("signature")}
        signature_changed = sorted(resigned)
        body_changed = sorted(
            k
            for k in survived - resigned
            if prev[k].get("body_hash") != curr[k].get("body_hash")
        )

        drift = {
            "added": [_label(k) for k in added],
            "removed": [_label(k) for k in removed],
            "signature_changed": [_label(k) for k in signature_changed],
            "body_changed": [_label(k) for k in body_changed],
        }

        drift_classifications: list[dict] = []
        if added or removed or body_changed:
            drift_classifications.append(
                {
                    "added": drift["added"],
                    "removed": drift["removed"],
                    "body_changed": drift["body_changed"],
                    "classification": self._classify(drift, governor),
                }
            )

        classifications = {d["classification"] for d in drift_classifications}
        if signature_changed:
            # No model needed and no model consulted: the parameter list of a
            # public function changed, which breaks callers whatever the intent.
            status = PluginStatus.FAIL.value
        elif "semantic" in classifications:
            status = PluginStatus.FAIL.value
        elif "unknown" in classifications:
            # An unclassified drift is still a drift. Reporting pass because the
            # model was unreachable is the same false green as reporting
            # semantic, pointed the other way.
            status = PluginStatus.UNKNOWN.value
        else:
            status = PluginStatus.PASS.value

        return PluginResult(
            status=status,
            findings=[
                {
                    "drift": drift,
                    "drift_classifications": drift_classifications,
                    "current_function_count": len(curr_funcs),
                    "previous_function_count": len(prev_funcs),
                    "scope": _SCOPE,
                }
            ],
        )

    def _classify(self, drift: dict, governor) -> str:
        """Ask once whether the drift is cosmetic. Anything unusable is unknown."""
        try:
            from api.llm.claude_client import ClaudeLLMClient

            llm = ClaudeLLMClient(model=governor.get_model("claude-haiku-4-5-20251001"))
            prompt = (
                "Classify this public-API drift — "
                f"added: {drift['added'][:_SAMPLE]}, "
                f"removed: {drift['removed'][:_SAMPLE]}, "
                f"changed bodies: {drift['body_changed'][:_SAMPLE]}. "
                "Is this 'safe' (formatting or renaming only) or 'semantic' "
                "(logic changed)? Reply with one word: safe or semantic."
            )
            raw = governor.cached_complete(prompt, llm.complete).strip().lower()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] drift classification unavailable: %s", self.name, exc)
            return "unknown"
        # Anything we did not ask for is "unknown", never "semantic".
        # complete() returns "" when the model was not reached, and the old
        # else-branch turned that outage into a definitive "the refactor changed
        # behaviour" verdict on every drifted function.
        return raw if raw in ("safe", "semantic") else "unknown"


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    ctx: dict = {"source_dir": ".", "trigger": "pre_refactor"}
    plugin = BehavioralEquivalencePlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in ("pass", "warn", "skip") else 1)
