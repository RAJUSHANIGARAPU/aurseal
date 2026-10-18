"""
Clean a ``playwright codegen`` TypeScript recording before it becomes a page object.

A raw recording is a transcript of a person at a keyboard: it signs in, accepts a
cookie banner, clicks a field before typing in it, retypes a value with the cursor
keys, clicks a backdrop by accident and clicks "Save" twice because the first click
did nothing yet. ``clean`` keeps what the flow means:

* sign-in and consent steps are dropped (the fixtures authenticate);
* cursor keys and retyping on a field collapse into its final value;
* a click straight before typing into the same field (a focus click) is dropped; a
  click on an overlay or backdrop becomes an explicit wait for it to go away;
* consecutive repeats of the same click keep only the last one;
* selectors are turned into registry entries of the most durable type the selector
  carries (``[data-testid=..]`` becomes ``testid``), and a selector built on an
  auto-generated id is listed for replacement by a semantic one.

What is left is grouped by intent (navigate, fill, submit, verify), and every
literal value lands in the test-data file, never in code. ``lint`` checks generated
code against the same rules.

    python -m tools.slash.recording clean recording.spec.ts [--name checkout] [--write]
    python -m tools.slash.recording lint --page P.py --test T.py --data D.json
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from core.locator_strategy import rank

_STR = r"""(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)"|`([^`]*)`)"""
_LOCATOR_CALL = re.compile(
    r"\b(getByRole|getByTestId|getByLabel|getByPlaceholder|getByText|getByAltText|getByTitle|locator)"
    r"\(\s*" + _STR + r"(\s*,\s*\{[^}]*\})?\s*\)"
)
_ACTION = re.compile(
    r"\.(click|dblclick|fill|press|check|uncheck|selectOption|type|pressSequentially)\((.*)\)\s*;?\s*$"
)
_GOTO = re.compile(r"page\.goto\(\s*" + _STR)
_EXPECT = re.compile(
    r"expect\((.*)\)\.(not\.)?(toBeVisible|toHaveText|toContainText|toHaveValue|toHaveURL|toBeChecked|toBeHidden)\((.*)\)"
)
_NAME = re.compile(r"name:\s*" + _STR)

SIGN_IN = re.compile(
    r"sign[ -]?in|log[ -]?in|log[ -]?on|password|passwort|wachtwoord|username|e-?mail address|"
    r"/login|/signin|/auth|oauth|/u/login|single sign|continue with",
    re.I,
)
CONSENT = re.compile(r"cookie|consent|accept all|agree|akkoord|reject all|privacy settings", re.I)
OVERLAY = re.compile(r"backdrop|overlay|cdk-overlay|modal-backdrop|scrim", re.I)
NAV_KEYS = re.compile(
    r"^(Arrow\w+|Home|End|Backspace|Delete|Tab|Shift\+\w+|Control\+a|Meta\+a|ControlOrMeta\+a)$"
)
AUTO_ID = re.compile(
    r"#[\w-]*(?:\d{2,}|[0-9a-f]{8,}|-\d+\b)|\[id=['\"]?[\w-]*\d{2,}|:r\d+:|\bmat-[a-z-]+-\d+"
)


def _s(groups: tuple) -> str:
    return next((g for g in groups if g is not None), "")


@dataclass
class Step:
    action: str  # goto | click | fill | press | check | uncheck | select | expect | wait_hidden
    locator: dict | None = None
    value: str = ""
    raw: str = ""
    assertion: str = ""

    @property
    def intent(self) -> str:
        if self.action == "goto":
            return "navigate"
        if self.action in ("fill", "check", "uncheck", "select"):
            return "fill"
        if self.action == "expect":
            return "verify"
        return "submit"

    def key_text(self) -> str:
        loc = self.locator or {}
        return str(loc.get("name") or loc.get("value") or "")


@dataclass
class CleanResult:
    steps: list[Step]
    dropped: list[tuple[str, str]] = field(default_factory=list)  # (raw line, reason)
    needs_semantic: list[str] = field(default_factory=list)

    def by_intent(self) -> dict[str, list[Step]]:
        out: dict[str, list[Step]] = {}
        for step in self.steps:
            out.setdefault(step.intent, []).append(step)
        return out


def to_entry(kind: str, value: str, options: str = "") -> dict:
    """A registry entry of the most durable type the selector carries."""
    if kind == "getByTestId":
        return {"type": "testid", "value": value}
    if kind == "getByLabel":
        return {"type": "label", "value": value}
    if kind == "getByPlaceholder":
        return {"type": "placeholder", "value": value}
    if kind in ("getByText", "getByAltText", "getByTitle"):
        return {"type": "text", "value": value}
    if kind == "getByRole":
        entry = {"type": "role", "role": value}
        name = _NAME.search(options or "")
        if name:
            entry["name"] = _s(name.groups())
        return entry
    for attr, kind_name in (
        ("data-testid", "testid"),
        ("aria-label", "aria_label"),
        ("placeholder", "placeholder"),
    ):
        m = re.fullmatch(rf"\[{attr}=['\"]?([^'\"\]]+)['\"]?\]", value.strip())
        if m:
            return {"type": kind_name, "value": m.group(1)}
    m = re.fullmatch(r"text=['\"]?(.+?)['\"]?", value.strip())
    if m:
        return {"type": "text", "value": m.group(1)}
    return {"type": "css", "value": value}


def parse_line(line: str) -> Step | None:
    text = line.strip()
    if not text or text.startswith(("//", "import ", "test(", "});", "}")):
        return None
    goto = _GOTO.search(text)
    if goto:
        return Step("goto", value=_s(goto.groups()[0:3]), raw=text)
    expect = _EXPECT.search(text)
    if expect:
        inner, negated, matcher, arg = expect.groups()
        loc = _LOCATOR_CALL.search(inner)
        entry = to_entry(loc.group(1), _s(loc.groups()[1:4]), loc.group(5) or "") if loc else None
        value_match = re.search(_STR, arg or "")
        value = _s(value_match.groups()) if value_match else ""
        return Step("expect", entry, value, text, f"{'not.' if negated else ''}{matcher}")
    loc = _LOCATOR_CALL.search(text)
    act = _ACTION.search(text)
    if not loc or not act:
        return None
    entry = to_entry(loc.group(1), _s(loc.groups()[1:4]), loc.group(5) or "")
    verb, arg = act.groups()
    value_match = re.search(_STR, arg or "")
    value = _s(value_match.groups()) if value_match else ""
    action = {
        "selectOption": "select",
        "type": "fill",
        "pressSequentially": "fill",
        "dblclick": "click",
    }.get(verb, verb)
    return Step(action, entry, value, text)


def _signature(entry: dict | None) -> str:
    return json.dumps(entry, sort_keys=True) if entry else ""


def clean(source: str) -> CleanResult:
    parsed = [s for s in (parse_line(line) for line in source.splitlines()) if s]
    result = CleanResult([])

    def drop(step: Step, reason: str) -> None:
        result.dropped.append((step.raw, reason))

    kept: list[Step] = []
    auth_field_sigs: set[str] = set()
    for step in parsed:
        loc_text = json.dumps(step.locator or {})
        if step.action == "goto" and SIGN_IN.search(step.value):
            drop(step, "sign-in page")
            continue
        if step.locator and SIGN_IN.search(loc_text):
            auth_field_sigs.add(_signature(step.locator))
            drop(step, "sign-in step")
            continue
        if step.locator and CONSENT.search(loc_text) and step.action != "expect":
            drop(step, "consent step")
            continue
        kept.append(step)

    # collapse retyping and cursor keys into the final value of each field
    collapsed: list[Step] = []
    for step in kept:
        sig = _signature(step.locator)
        if step.action == "press" and NAV_KEYS.match(step.value):
            drop(step, "cursor or editing key")
            continue
        if (
            step.action == "fill"
            and collapsed
            and collapsed[-1].action == "fill"
            and _signature(collapsed[-1].locator) == sig
        ):
            drop(collapsed.pop(), "retyped: only the final value is kept")
        collapsed.append(step)

    final: list[Step] = []
    for index, step in enumerate(collapsed):
        sig = _signature(step.locator)
        nxt = collapsed[index + 1] if index + 1 < len(collapsed) else None
        if step.action == "click" and OVERLAY.search(json.dumps(step.locator or {})):
            drop(step, "overlay click replaced by a wait for it to close")
            final.append(Step("wait_hidden", step.locator, raw=step.raw))
            continue
        if (
            step.action == "click"
            and nxt
            and nxt.action == "fill"
            and _signature(nxt.locator) == sig
        ):
            drop(step, "focus click before typing")
            continue
        if (
            step.action == "click"
            and nxt
            and nxt.action == "click"
            and _signature(nxt.locator) == sig
        ):
            drop(step, "repeated click: the last attempt is kept")
            continue
        final.append(step)

    result.steps = final
    for step in final:
        loc = step.locator or {}
        if loc.get("type") == "css" and AUTO_ID.search(str(loc.get("value", ""))):
            result.needs_semantic.append(str(loc["value"]))
    return result


def ident(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "item"


_ROLE_SUFFIX = {
    "button": "button",
    "link": "link",
    "textbox": "field",
    "checkbox": "checkbox",
    "combobox": "select",
    "heading": "heading",
    "option": "option",
    "tab": "tab",
}


def locator_key(entry: dict, action: str) -> str:
    base = ident(str(entry.get("name") or entry.get("value") or entry.get("role") or "element"))[
        :40
    ]
    suffix = _ROLE_SUFFIX.get(str(entry.get("role", "")))
    if not suffix:
        suffix = {"fill": "field", "select": "select", "check": "checkbox", "expect": "text"}.get(
            action, "element"
        )
    return base if base.endswith(suffix) else f"{base}_{suffix}"


def build_locators(result: CleanResult) -> dict[str, dict]:
    out: dict[str, dict] = {}
    seen: dict[str, str] = {}
    for step in result.steps:
        if not step.locator:
            continue
        sig = _signature(step.locator)
        if sig in seen:
            continue
        key = locator_key(step.locator, step.action)
        while key in out:
            key += "_2"
        out[key] = dict(step.locator)
        seen[sig] = key
    return dict(sorted(out.items(), key=lambda kv: kv[0]))


def build_data(result: CleanResult, locators: dict[str, dict]) -> dict[str, str]:
    keys = {_signature(v): k for k, v in locators.items()}
    data: dict[str, str] = {}
    for step in result.steps:
        if step.action == "goto":
            data.setdefault("start_path", urlsplit(step.value).path or "/")
        elif step.value and step.locator:
            name = keys.get(_signature(step.locator), "value")
            suffix = "expected" if step.action == "expect" else "value"
            key = f"{name}_{suffix}"
            while key in data and data[key] != step.value:
                key += "_2"
            data[key] = step.value
        elif step.action == "expect" and step.assertion == "toHaveURL" and step.value:
            data["expected_url"] = step.value
    return data


def flow_name(source: str) -> str:
    for line in source.splitlines():
        m = _GOTO.search(line)
        if m and not SIGN_IN.search(_s(m.groups()[0:3])):
            parts = urlsplit(_s(m.groups()[0:3]))
            segments = [s for s in parts.path.split("/") if s]
            return ident(segments[-1] if segments else (parts.hostname or "flow").split(".")[0])
    return "flow"


def merge(existing: dict, new: dict) -> dict:
    """Existing entries win: a re-recorded flow extends a file, never rewrites it."""
    merged = dict(existing)
    for key, value in new.items():
        merged.setdefault(key, value)
    return merged


# ---------------------------------------------------------------------- lint

_SLEEP = re.compile(r"\btime\.sleep\(|\bwait_for_timeout\(|\basyncio\.sleep\(")
_RAW_SELECTOR_CALLS = {
    "locator",
    "get_by_role",
    "get_by_text",
    "get_by_test_id",
    "get_by_label",
    "get_by_placeholder",
    "query_selector",
    "query_selector_all",
    "get_by_alt_text",
    "get_by_title",
}


def lint(page_src: str, test_src: str, data: dict | None = None) -> list[str]:
    problems: list[str] = []
    for label, src in (("page object", page_src), ("test", test_src)):
        if _SLEEP.search(src):
            problems.append(f"{label}: contains a sleep")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if (
                    node.func.attr in _RAW_SELECTOR_CALLS
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                ):
                    problems.append(
                        f"{label}: raw selector {node.func.attr}({node.args[0].value!r})"
                    )
            if (
                label == "test"
                and isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "page"
            ):
                problems.append(f"test: calls Playwright directly (page.{node.attr})")
            if data and isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in {str(v) for v in data.values() if len(str(v)) > 1}:
                    problems.append(
                        f"{label}: literal {node.value!r} belongs in the test-data file"
                    )
    page_tree = ast.parse(page_src)
    bases = [ast.unparse(b) for n in page_tree.body if isinstance(n, ast.ClassDef) for b in n.bases]
    if not any(b.endswith("BasePage") for b in bases):
        problems.append("page object: does not extend BasePage")
    return list(dict.fromkeys(problems))


def lint_locators(locators: dict) -> list[str]:
    """CSS is acceptable only when nothing more durable was available in the recording."""
    return [
        f"{k}: {v['type']} (rank {rank(v)})"
        for k, v in locators.items()
        if v.get("type") == "css"
        and re.search(r"data-testid|aria-label|placeholder=", str(v.get("value")))
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clean a codegen recording; lint generated code.")
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("clean")
    c.add_argument("recording", type=Path)
    c.add_argument("--name")
    c.add_argument("--write", action="store_true", help="write/extend the locator and data files")
    l_ = sub.add_parser("lint")
    l_.add_argument("--page", type=Path, required=True)
    l_.add_argument("--test", type=Path, required=True)
    l_.add_argument("--data", type=Path)
    args = parser.parse_args(argv)

    if args.command == "lint":
        data = json.loads(args.data.read_text(encoding="utf-8")) if args.data else None
        problems = lint(
            args.page.read_text(encoding="utf-8"), args.test.read_text(encoding="utf-8"), data
        )
        for p in problems:
            print(p)
        print("lint: clean" if not problems else f"lint: {len(problems)} problem(s)")
        return 1 if problems else 0

    source = args.recording.read_text(encoding="utf-8")
    name = args.name or flow_name(source)
    result = clean(source)
    locators = build_locators(result)
    data = build_data(result, locators)
    targets = {
        "page object": Path(f"ui/pages/{name}_page.py"),
        "test": Path(f"ui/tests/test_{name}.py"),
        "locators": Path(f"ui/locators/{name}_locators.json"),
        "data": Path(f"ui/testdata/{name}.json"),
    }
    print(f"flow: {name}")
    for label, path in targets.items():
        print(f"{label}: {path} ({'exists - extend it' if path.exists() else 'new'})")
    for intent, steps in result.by_intent().items():
        print(f"\n[{intent}]")
        for step in steps:
            print(
                f"- {step.action} {json.dumps(step.locator) if step.locator else ''} {step.value!r}"
            )
    if result.dropped:
        print("\n[dropped]")
        print("\n".join(f"- {reason}: {raw}" for raw, reason in result.dropped))
    if result.needs_semantic:
        print("\n[replace with a semantic selector]")
        print("\n".join(f"- {sel}" for sel in result.needs_semantic))
    if args.write:
        for key, new in (("locators", locators), ("data", data)):
            path = targets[key]
            old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(merge(old, new), indent=2) + "\n", encoding="utf-8")
            print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
