"""Command line for the workflow generators.

::

    python -m generators.cli spec     demo_sut/config/subscription.machine.yaml [--out specs]
    python -m generators.cli graph    demo_sut/config/subscription.machine.yaml
    python -m generators.cli generate demo_sut/config/subscription.machine.yaml [--timeout 420]
    python -m generators.cli convert  recordings/checkout.py --name checkout

* ``spec`` writes ``<out>/<key>.md`` (no model call).
* ``graph`` prints the ASCII graph, the unreachable states and the path count; exit 1
  when the definition has consistency problems.
* ``generate`` runs the pipeline: workflow file -> spec -> ``TestGenerator.generate_from_workflow``
  -> ``ui/pages/<key>_page.py`` + ``ui/tests/test_<key>_generated.py``.
* ``convert`` turns a ``playwright codegen`` recording (Python or TypeScript) into
  ``ui/pages/<name>_page.py``, ``ui/tests/test_<name>.py`` and ``ui/locators/<name>_locators.json``.

Model calls are bounded by ``--timeout`` (env ``GENERATOR_TIMEOUT_S``, default 420 s) with a
progress line every ``GENERATOR_PROGRESS_S`` seconds (default 30). Nothing is written unless
the whole generation succeeded: a failure, a timeout or an incomplete completion leaves every
target file exactly as it was. Each command prints the next steps.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import TypeVar

from core.ai.test_generator import GeneratedFiles, TestGenerator
from generators.spec_writer import write_spec
from generators.state_graph import StateGraph
from generators.workflow_model import WorkflowError, load

TIMEOUT_ENV = "GENERATOR_TIMEOUT_S"
PROGRESS_ENV = "GENERATOR_PROGRESS_S"
DEFAULT_TIMEOUT_S = 420.0
DEFAULT_PROGRESS_S = 30.0

T = TypeVar("T")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def bounded(
    call: Callable[[], T | None], timeout_s: float, progress_s: float | None = None, label=""
) -> T | None:
    """Run ``call`` with a deadline; ``None`` on timeout. Logs progress while waiting."""
    progress_s = progress_s or _env_float(PROGRESS_ENV, DEFAULT_PROGRESS_S)
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(call)
    waited = 0.0
    try:
        while True:
            step = min(progress_s, timeout_s - waited)
            try:
                return future.result(timeout=max(step, 0.0))
            except FutureTimeout:
                waited += step
                if waited >= timeout_s:
                    print(f"{label or 'generation'} did not finish within {timeout_s:.0f}s")
                    return None
                print(f"... {label or 'generation'} still running ({waited:.0f}s)")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def write_all(files: dict[Path, str]) -> list[Path]:
    """Write every file (parents created). Called only after the whole generation succeeded."""
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return list(files)


def cmd_spec(args: argparse.Namespace) -> int:
    definition = load(args.workflow)
    path = write_spec(definition, args.out)
    print(f"spec written: {path}")
    print("next steps:")
    print(f"  review {path}, then: python -m generators.cli generate {args.workflow}")
    return 0


def cmd_graph(args: argparse.Namespace) -> int:
    definition = load(args.workflow)
    graph = StateGraph(definition)
    print(graph.ascii())
    print(f"paths: {len(graph.paths())}")
    problems = definition.problems()
    for problem in problems:
        print(f"problem: {problem}")
    return 1 if problems else 0


def cmd_generate(args: argparse.Namespace, generator: TestGenerator | None = None) -> int:
    definition = load(args.workflow)
    spec_path = write_spec(definition, args.spec_dir)
    print(f"spec written: {spec_path}")
    key = definition.key
    generator = generator or TestGenerator()
    result: GeneratedFiles | None = bounded(
        lambda: generator.generate_from_workflow(spec_path, key), args.timeout, label=key
    )
    if result is None:
        print("generation failed: no page object or test was written")
        return 1
    written = write_all(
        {
            Path(args.page_dir) / f"{key}_page.py": result.page_object,
            Path(args.test_dir) / f"test_{key}_generated.py": result.test,
        }
    )
    for path in written:
        print(f"written: {path}")
    print("next steps:")
    print(f"  pytest {written[1]} -n 0   # run it, then review the page object")
    print(f"  git diff {' '.join(str(p) for p in written)}")
    return 0


def convert_recording(
    recording: str | Path,
    name: str,
    *,
    page_dir: str | Path = "ui/pages",
    test_dir: str | Path = "ui/tests",
    locator_dir: str | Path = "ui/locators",
    timeout_s: float | None = None,
    generator: TestGenerator | None = None,
) -> list[Path] | None:
    """Convert a codegen recording; the written paths, or None when nothing was written."""
    generator = generator or TestGenerator()
    timeout_s = timeout_s if timeout_s is not None else _env_float(TIMEOUT_ENV, DEFAULT_TIMEOUT_S)
    result = bounded(lambda: generator.from_recording(Path(recording), name), timeout_s, label=name)
    if result is None:
        return None
    return write_all(
        {
            Path(page_dir) / f"{name}_page.py": result.page_object,
            Path(test_dir) / f"test_{name}.py": result.test,
            Path(locator_dir) / f"{name}_locators.json": json.dumps(result.locators, indent=2)
            + "\n",
        }
    )


def cmd_convert(args: argparse.Namespace, generator: TestGenerator | None = None) -> int:
    written = convert_recording(
        args.recording,
        args.name,
        page_dir=args.page_dir,
        test_dir=args.test_dir,
        locator_dir=args.locator_dir,
        timeout_s=args.timeout,
        generator=generator,
    )
    if written is None:
        print("conversion failed: nothing was written")
        return 1
    for path in written:
        print(f"written: {path}")
    print("next steps:")
    print(f"  pytest {written[1]} -n 0   # run it two or three times before committing")
    print(
        f'  python -c "from core.locator_strategy import lint_registry; import json; '
        f"print(lint_registry(json.load(open('{written[2]}'))))\""
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m generators.cli", description=__doc__.split("\n")[0]
    )
    sub = parser.add_subparsers(dest="command", required=True)
    timeout = _env_float(TIMEOUT_ENV, DEFAULT_TIMEOUT_S)

    p = sub.add_parser("spec", help="workflow YAML -> Markdown spec")
    p.add_argument("workflow")
    p.add_argument("--out", default="specs")
    p.set_defaults(run=cmd_spec)

    p = sub.add_parser("graph", help="print the state graph and unreachable states")
    p.add_argument("workflow")
    p.set_defaults(run=cmd_graph)

    p = sub.add_parser("generate", help="workflow YAML -> spec -> page object + pytest module")
    p.add_argument("workflow")
    p.add_argument("--spec-dir", default="specs")
    p.add_argument("--page-dir", default="ui/pages")
    p.add_argument("--test-dir", default="ui/tests")
    p.add_argument("--timeout", type=float, default=timeout)
    p.set_defaults(run=cmd_generate)

    p = sub.add_parser("convert", help="codegen recording -> page object + test + locators")
    p.add_argument("recording")
    p.add_argument("--name", required=True)
    p.add_argument("--page-dir", default="ui/pages")
    p.add_argument("--test-dir", default="ui/tests")
    p.add_argument("--locator-dir", default="ui/locators")
    p.add_argument("--timeout", type=float, default=timeout)
    p.set_defaults(run=cmd_convert)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.run(args)
    except WorkflowError as exc:
        print(f"error: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
