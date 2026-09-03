"""
Integration graph plugin — maps service-to-service calls and generates tests.

``status="pass"`` was unconditional. The plugin could not report anything else,
and three separate ways of learning nothing all arrived at the same green:

* ``source_dir`` defaulted to ``"."``, so ``rglob("*.py")`` walked the
  virtualenv — 3,353 of 3,574 files under ``site-packages``, ``ast.parse``d at a
  measured 25.9s per run and then repeated by ``BasePlugin.execute``'s three
  retries;
* zero endpoints found — an empty tree, a missing directory, or source that
  simply makes no HTTP calls — reported ``pass`` with an empty map;
* every endpoint got the same generated probe: ``requests.get(url)`` and
  ``assert resp.status_code == 200``. Against this repository the one endpoint
  it finds is ``https://api.anthropic.com/v1/messages``, from the framework's own
  LLM client, which is POST-only and authenticated — so the generated test can
  only ever fail, and the plugin still said ``pass``.

It now maps the call's method as well as its URL, generates a probe only where a
GET probe is meaningful, and can report ``fail``: a literal URL with no
``http``/``https`` scheme is not a service this code can reach, it is a call
that raises ``MissingSchema``/``InvalidSchema`` the moment it executes.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from urllib.parse import urlsplit

from plugins._base_plugin import (
    BasePlugin,
    PluginPriority,
    PluginResult,
    PluginStatus,
    is_passing,
)
from plugins.cost_governor import CostGovernor
from plugins.tier1._paths import iter_source_files, resolve_dir

_HTTP_METHODS = ("get", "post", "put", "delete", "patch")

#: Schemes ``requests`` has an adapter for. Anything else raises before a socket
#: is opened, so it is a defect in the source and not a service being down.
_ROUTABLE_SCHEMES = frozenset({"http", "https"})

_MAX_GENERATED = 10


class _CallVisitor(ast.NodeVisitor):
    """Collects ``requests.<method>("<literal>")`` calls, method included.

    The method was previously discarded, which is why every endpoint — POST,
    DELETE, whatever — was handed the same GET probe.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.calls: list[dict] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in _HTTP_METHODS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "requests"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            self.calls.append(
                {
                    "method": node.func.attr,
                    "url": node.args[0].value,
                    "file": str(self.path),
                    "line": node.lineno,
                }
            )
        self.generic_visit(node)


class IntegrationGraphPlugin(BasePlugin):
    name = "integration-graph"
    priority = PluginPriority.NORMAL
    trigger_conditions = ["dependency_change"]

    def run(self, context: dict) -> PluginResult:
        source_dir = resolve_dir(context, "source_dir", ".")
        if not source_dir.is_dir():
            return _unknown(f"source_dir {source_dir} does not exist — nothing was mapped")

        calls: list[dict] = []
        dep_map: dict[str, list[str]] = {}
        scanned = 0
        unparseable: list[dict] = []

        for src_file in iter_source_files(source_dir):
            try:
                tree = ast.parse(src_file.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, SyntaxError, ValueError) as exc:
                unparseable.append({"file": str(src_file), "error": f"{type(exc).__name__}: {exc}"})
                continue
            scanned += 1
            visitor = _CallVisitor(src_file)
            visitor.visit(tree)
            if visitor.calls:
                calls.extend(visitor.calls)
                dep_map[str(src_file)] = [c["url"] for c in visitor.calls]

        base_finding = {
            "source_dir": str(source_dir),
            "dependency_map": dep_map,
            "files_scanned": scanned,
            "files_unparseable": len(unparseable),
        }

        if context.get("dry_run"):
            # A dry run maps but generates nothing, so it holds no evidence
            # about the services. It used to report `pass` for that.
            return PluginResult(
                status=PluginStatus.SKIP.value,
                findings=[{**base_finding, "reason": "dry run — nothing generated"}],
                dry_run=True,
            )

        if scanned == 0:
            return _unknown(f"no readable Python file under {source_dir} — nothing was mapped")
        if not calls:
            return _unknown(
                f"scanned {scanned} file(s) under {source_dir} and found no "
                f'requests.<method>("<literal url>") call — no dependency was verified'
            )

        unroutable: list[dict] = []
        probeable: list[dict] = []
        # A non-GET dependency is real and belongs on the map, but a GET probe
        # asserting 200 against it is a test that can only fail. Say so instead
        # of writing it.
        not_probeable: list[dict] = []
        for call in calls:
            if urlsplit(call["url"]).scheme not in _ROUTABLE_SCHEMES:
                unroutable.append(call)
            elif call["method"] == "get":
                probeable.append(call)
            else:
                not_probeable.append(call)

        governor = context.get("cost_governor") or CostGovernor()
        out_dir = resolve_dir(context, "out_dir", "ai_generated_tests/integration")
        out_dir.mkdir(parents=True, exist_ok=True)

        unique_probeable = list(dict.fromkeys(c["url"] for c in probeable))
        generated: list[str] = []
        for endpoint in unique_probeable[:_MAX_GENERATED]:
            slug = endpoint.replace("://", "_").replace("/", "_").replace(".", "_")[:40]
            out_file = out_dir / f"test_integration_{slug}.py"
            out_file.write_text(
                f'"""Auto-generated integration test for {endpoint}."""\n\n'
                "import requests\n\n\n"
                f"def test_endpoint_{slug}() -> None:\n"
                f'    """Verify {endpoint} returns 200."""\n'
                f'    resp = requests.get("{endpoint}", timeout=10)\n'
                "    assert resp.status_code == 200\n",
                encoding="utf-8",
            )
            generated.append(str(out_file))
            governor.record("none", 0, 0.0)

        finding = {
            **base_finding,
            "endpoints": len(calls),
            "generated_files": generated,
            "unroutable_endpoints": unroutable,
            "not_probeable_endpoints": not_probeable,
            "not_generated_over_cap": max(0, len(unique_probeable) - _MAX_GENERATED),
        }

        if unroutable:
            finding["reason"] = (
                f"{len(unroutable)} literal URL(s) carry no http/https scheme; requests raises "
                f"MissingSchema/InvalidSchema on these before any request is sent"
            )
            return PluginResult(status=PluginStatus.FAIL.value, findings=[finding])

        if not generated:
            finding["reason"] = (
                f"{len(calls)} dependency call(s) mapped, none of them a GET — "
                f"a GET probe asserting 200 would be a test that cannot pass"
            )
            return PluginResult(status=PluginStatus.WARN.value, findings=[finding])

        status = PluginStatus.WARN.value if not_probeable else PluginStatus.PASS.value
        return PluginResult(status=status, findings=[finding])


def _unknown(reason: str) -> PluginResult:
    return PluginResult(status=PluginStatus.UNKNOWN.value, findings=[{"reason": reason}])


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    ctx: dict = {}
    plugin = IntegrationGraphPlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if is_passing(result.status) or result.status == PluginStatus.SKIP.value else 1)
