"""
Requirements text -> scenario specs in the ``specs/`` format.

* ``ingest`` reads a file, fetches a URL (HTML reduced to plain text) or takes inline
  text, capped at 8,000 characters with a notice;
* ``vague`` lists why a text is too thin to test (too short, no observable outcome);
  any reason means: ask for clarification and write nothing;
* ``write`` turns a JSON list of scenarios into ``specs/<feature>.md`` after checking
  that each of the four groups - main path, validation, boundary, prevented - has a
  scenario or an explicit "not applicable" reason.

    python -m tools.slash.prd ingest <file|url|"inline text">
    python -m tools.slash.prd vague <file|url|"inline text">
    python -m tools.slash.prd write --feature checkout --scenarios scenarios.json [--specs specs]

Scenario JSON: ``{"groups_not_applicable": {"boundary": "why"}, "scenarios": [{"name":
..., "group": "main|validation|boundary|prevented", "preconditions": [...], "steps":
[...], "expected": [...], "tags": ["ui", "smoke"]}]}``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

CAP = 8000
GROUPS = ("main", "validation", "boundary", "prevented")
TAGS = {"ui", "api", "smoke", "regression"}
_OUTCOME = re.compile(
    r"\b(should|must|shall|can(?:not)?|will|shows?|displays?|returns?|receives?|sees?|"
    r"rejects?|refuses?|accepts?|allows?|prevents?|error|message|then|redirect\w*|saved?|"
    r"sends?|creates?|deletes?|updates?|lists?)\b",
    re.I,
)


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head"}
    BLOCK = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skipping += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skipping:
            self.skipping -= 1

    def handle_data(self, data):
        if not self.skipping:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    text = "".join(parser.parts)
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def cap(text: str, limit: int = CAP) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[cut at {limit} of {len(text)} characters]"


def ingest(source: str, fetch=None) -> str:
    if source.startswith(("http://", "https://")):
        if fetch is None:
            with urllib.request.urlopen(source, timeout=15) as response:  # noqa: S310 - user-given URL
                raw = response.read(2 * 1024 * 1024).decode("utf-8", errors="replace")
        else:
            raw = fetch(source)
        return cap(html_to_text(raw))
    path = Path(source)
    if len(source) < 260 and path.suffix and path.exists():
        return cap(path.read_text(encoding="utf-8"))
    return cap(source.strip())


def vague(text: str) -> list[str]:
    reasons = []
    words = re.findall(r"[A-Za-z]{2,}", text)
    if len(words) < 6:
        reasons.append(f"only {len(words)} word(s): not enough to know what to test")
    if not _OUTCOME.search(text):
        reasons.append("no observable outcome (what should happen, be shown, refused or saved)")
    return reasons


@dataclass
class Plan:
    scenarios: list[dict]
    not_applicable: dict[str, str]


def check(plan: Plan) -> list[str]:
    problems = []
    for group in GROUPS:
        has = any(s.get("group") == group for s in plan.scenarios)
        reason = (plan.not_applicable.get(group) or "").strip()
        if not has and not reason:
            problems.append(f"group '{group}' has no scenario and no not-applicable reason")
    for s in plan.scenarios:
        name = s.get("name", "?")
        if s.get("group") not in GROUPS:
            problems.append(f"{name}: group must be one of {', '.join(GROUPS)}")
        for part in ("steps", "expected"):
            if not s.get(part):
                problems.append(f"{name}: needs {part}")
        unknown = set(s.get("tags") or []) - TAGS - {"follow-up"}
        if unknown:
            problems.append(f"{name}: unknown tags {sorted(unknown)}")
    names = [s.get("name", "").lower() for s in plan.scenarios]
    if len(names) != len(set(names)):
        problems.append("scenario names must be unique")
    return problems


def render(feature: str, plan: Plan) -> str:
    out = [
        "---",
        "seed: true",
        f"feature: {feature}",
        "---",
        "",
        f"# {feature.replace('_', ' ').capitalize()}",
        "",
    ]
    for s in plan.scenarios:
        out += [f"## Scenario: {s['name']}", "", "### Preconditions"]
        out += [f"- {p}" for p in s.get("preconditions") or ["none"]]
        out += ["", "### Steps"]
        out += [f"{i}. {step}" for i, step in enumerate(s["steps"], 1)]
        out += ["", "### Expected"]
        out += [f"- {e}" for e in s["expected"]]
        tags = list(s.get("tags") or []) + [s["group"]]
        out += ["", "### Tags", " ".join(f"`{t}`" for t in tags), ""]
    return "\n".join(out)


def write(feature: str, plan: Plan, specs: Path = Path("specs")) -> Path:
    problems = check(plan)
    if problems:
        raise ValueError("; ".join(problems))
    if not re.fullmatch(r"[a-z][a-z0-9_]*", feature):
        raise ValueError("feature must be snake_case")
    path = specs / f"{feature.replace('_', '-')}.md"
    path.write_text(render(feature, plan), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Requirements -> scenario specs.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("ingest", "vague"):
        sub.add_parser(name).add_argument("source")
    w = sub.add_parser("write")
    w.add_argument("--feature", required=True)
    w.add_argument("--scenarios", type=Path, required=True)
    w.add_argument("--specs", type=Path, default=Path("specs"))
    args = parser.parse_args(argv)
    if args.command == "ingest":
        print(ingest(args.source))
        return 0
    if args.command == "vague":
        reasons = vague(ingest(args.source))
        for reason in reasons:
            print(f"too vague: {reason}")
        if not reasons:
            print("testable")
        return 1 if reasons else 0
    data = json.loads(args.scenarios.read_text(encoding="utf-8"))
    plan = Plan(data.get("scenarios") or [], data.get("groups_not_applicable") or {})
    try:
        print(f"wrote {write(args.feature, plan, args.specs)}")
    except ValueError as err:
        print(f"not written: {err}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
