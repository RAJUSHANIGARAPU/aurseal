"""
Allure ``categories.json``: bucket failures and skips by what they mean.

An Allure report without categories lists every red test as "Product defects",
which is wrong for most of them. These buckets answer the first triage question —
*whose problem is this?* — before anyone opens a test:

* the ``known_defect`` lifecycle (``verification/pytest_plugin.py``): an expected
  red that **stopped reproducing** (strict XPASS, the defect may be fixed) is its
  own loud bucket, and one still reproducing is filed as expected;
* infrastructure: backend 5xx / transient network, authentication, configuration;
* test-side: broken locators, timeouts, harness errors (Allure's ``broken`` status
  is an exception other than an assertion — usually the test, not the product);
* skips by reason: known product bug, environment limitation, superseded
  coverage, harness / on-demand — so a growing skip count can be read at a glance;
* everything else that failed an assertion: product defects.

Allure assigns the *first* category whose status matches and whose
``messageRegex`` matches the *whole* message (``Pattern.matches``), so order is
precedence and every pattern starts with ``(?s)`` to span multi-line messages.
``categorize`` reproduces that rule so the table can be unit-tested.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

DEFAULT_CATEGORIES: list[dict] = [
    {
        "name": "Known defect no longer reproduces (verify the fix, then drop the marker)",
        "messageRegex": r"(?s).*XPASS\(strict\).*known defect.*",
        "matchedStatuses": ["failed"],
    },
    {
        "name": "Known defect still reproduces (expected red)",
        "messageRegex": r"(?s).*known defect .* is open.*",
        "matchedStatuses": ["skipped"],
    },
    {
        "name": "Backend 5xx / transient network",
        "messageRegex": (
            r"(?s).*(HTTP 5\d\d|\b50[234]\b|Bad Gateway|Service Unavailable|Gateway Time-?out"
            r"|[Cc]onnection (reset|refused|aborted)|ERR_CONNECTION|ECONNRESET|net::ERR_).*"
        ),
        "matchedStatuses": ["failed", "broken"],
    },
    {
        "name": "Authentication",
        "messageRegex": r"(?s).*(\b401\b|\b403\b|[Uu]nauthori[sz]ed|[Ff]orbidden|login page).*",
        "matchedStatuses": ["failed", "broken"],
    },
    {
        "name": "Configuration / setup",
        "messageRegex": (
            r"(?s).*(Missing config key|Unknown env|Unknown auth mode|fixture '.*' not found).*"
        ),
        "matchedStatuses": ["failed", "broken"],
    },
    {
        "name": "Broken locators",
        "messageRegex": (
            r"(?s).*(strict mode violation|waiting for (get_by_|locator)|resolved to 0 elements"
            r"|element is not (attached|visible|enabled)|Unsupported locator type).*"
        ),
        "matchedStatuses": ["failed", "broken"],
    },
    {
        "name": "Timeouts",
        "messageRegex": r"(?s).*([Tt]imeout .*exceeded|TimeoutError|timed out).*",
        "matchedStatuses": ["failed", "broken"],
    },
    {
        "name": "Skipped: known product bug",
        "messageRegex": r"(?s).*(known product bug|blocked by [A-Z][A-Z0-9]+-\d+).*",
        "matchedStatuses": ["skipped"],
    },
    {
        "name": "Skipped: environment limitation (not a defect)",
        "messageRegex": (
            r"(?s).*(environment limitation|not available (on|in) this environment"
            r"|no test data|could not seed|requires (a |the )?(live|staging|network)).*"
        ),
        "matchedStatuses": ["skipped"],
    },
    {
        "name": "Skipped: superseded or duplicate coverage",
        "messageRegex": r"(?s).*(superseded|duplicate(s|d)? coverage).*",
        "matchedStatuses": ["skipped"],
    },
    {
        "name": "Skipped: harness / on-demand (not product signal)",
        "messageRegex": r"(?s).*(on-demand|opt-in|harness|ENABLE_[A-Z_]+).*",
        "matchedStatuses": ["skipped"],
    },
    {
        "name": "Test or harness errors",
        "matchedStatuses": ["broken"],
    },
    {
        "name": "Product defects",
        "matchedStatuses": ["failed"],
    },
]


def categorize(status: str, message: str, categories: list[dict] | None = None) -> str | None:
    """The category Allure would assign, by its first-match, whole-message rule."""
    for category in DEFAULT_CATEGORIES if categories is None else categories:
        if status not in category.get("matchedStatuses", []):
            continue
        pattern = category.get("messageRegex")
        if pattern is None or re.fullmatch(pattern, message or ""):
            return category["name"]
    return None


def write_categories(results_dir: str | Path, categories: list[dict] | None = None) -> Path:
    """Write ``categories.json`` into the Allure results directory."""
    for category in DEFAULT_CATEGORIES if categories is None else categories:
        if "messageRegex" in category:
            re.compile(category["messageRegex"])  # a bad pattern fails here, not in the report
    path = Path(results_dir) / "categories.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(DEFAULT_CATEGORIES if categories is None else categories, indent=2),
        encoding="utf-8",
    )
    return path
