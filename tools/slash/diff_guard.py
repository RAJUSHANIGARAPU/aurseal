"""
Refuse a "fix" that changes what a test expects.

The run-fix loop may repair selectors, waits and synchronisation in page objects
and helpers. It may not make a test pass by editing the test: no changed line in
a test module, in test data, or anywhere that is an assertion or a Playwright
expectation. This reads a unified diff and lists every line that breaks the rule.

    git diff HEAD | python -m tools.slash.diff_guard
"""

from __future__ import annotations

import re
import sys

_ASSERTION = re.compile(
    r"^\s*assert\b|\bexpect\(|\.(?:to|not_to)_[a-z_]+\(|pytest\.approx|\bassert_[a-z_]+\("
)
_PROTECTED_PATH = re.compile(
    r"(^|/)(test_[^/]*\.py|[^/]*_test\.py|conftest\.py)$|(^|/)(testdata|test_data|fixtures)/"
)


def violations(diff: str) -> list[str]:
    found: list[str] = []
    path = ""
    for line in diff.splitlines():
        if line.startswith(("+++ ", "--- ")):
            target = line[4:].strip()
            path = target[2:] if target.startswith(("a/", "b/")) else target
            if path != "/dev/null" and _PROTECTED_PATH.search(path):
                found.append(f"{path}: test modules and test data are not touched by a fix")
            continue
        if line.startswith("@@"):
            continue
        if line[:1] in "+-" and _ASSERTION.search(line[1:]):
            found.append(f"{path}: changes an assertion: {line[1:].strip()}")
    return list(dict.fromkeys(found))


def main() -> int:
    problems = violations(sys.stdin.read())
    for problem in problems:
        print(problem)
    if problems:
        print("refused: a fix may change selectors, waits and synchronisation only")
        return 1
    print("diff guard: only page-object and helper changes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
