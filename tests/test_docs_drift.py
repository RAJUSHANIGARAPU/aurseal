"""The docs are checked against the code, so they cannot quietly drift.

Module paths, pytest markers and environment variables named in the docs must exist; the
"what runs where" table must agree with the lane policy and the tests; the CI decision
sections must be linked, and every link to them must resolve.
"""

from __future__ import annotations

import configparser
import functools
import importlib.util
import re
import shlex
from pathlib import Path

import pytest
import yaml

from gate.lanes import LANES, excluded_markers

ROOT = Path(__file__).resolve().parents[1]
GUIDES = [ROOT / "README.md", ROOT / "CLAUDE.md", *sorted((ROOT / "docs").glob("*.md"))]
FEATURES = sorted((ROOT / "docs" / "features").glob("*.md"))
NEW_DOCS = [ROOT / "docs" / n for n in ("whats-live.md", "ci-decisions.md", "green-is-evidence.md")]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
_FILE_SUFFIXES = {
    "txt", "md", "json", "jsonl", "ndjson", "yml", "yaml", "ini", "toml", "py", "cfg", "db",
    "xml", "html", "zip", "csv", "png", "properties", "lock", "sh", "tpl", "pdf", "webm",
}  # fmt: skip
_TOP_LEVEL = {
    p.name if p.is_dir() else p.stem
    for p in ROOT.iterdir()
    if (p.is_dir() and (p / "__init__.py").exists()) or (p.suffix == ".py" and p.is_file())
}


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _backticked(text: str) -> list[str]:
    return re.findall(r"`([^`\n]+)`", text)


# ------------------------------------------------------------------ module paths


def _resolves(dotted: str) -> bool:
    """The longest module prefix exists; one trailing part may be a name defined in it."""
    parts = dotted.split(".")
    for cut in range(len(parts), 0, -1):
        module = ".".join(parts[:cut])
        try:
            spec = importlib.util.find_spec(module)
        except (ModuleNotFoundError, ValueError):
            spec = None
        if spec is None:
            continue
        rest = parts[cut:]
        if not rest:
            return True
        if len(rest) > 2 or spec.origin is None or not spec.origin.endswith(".py"):
            return False
        source = Path(spec.origin).read_text(encoding="utf-8")
        name = rest[0]
        return bool(
            re.search(rf"^\s*(def|class|async def)\s+{name}\b|^{name}\s*[:=]", source, re.M)
        )
    return False


def _module_mentions(path: Path) -> set[str]:
    found = set()
    for token in _backticked(_text(path)):
        token = token.strip().rstrip("()")
        if not re.fullmatch(r"[a-z_][a-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+", token):
            continue
        if token.split(".")[0] not in _TOP_LEVEL or token.split(".")[-1] in _FILE_SUFFIXES:
            continue
        found.add(token)
    return found


def test_the_module_check_can_fail():
    assert _resolves("gate.lanes.bringup_command") and _resolves("gate.workflow_lint")
    assert not _resolves("gate.no_such_module") and not _resolves("gate.lanes.no_such_name")


@pytest.mark.parametrize("path", GUIDES, ids=lambda p: p.name)
def test_every_module_path_named_in_the_guides_exists(path):
    missing = sorted(m for m in _module_mentions(path) if not _resolves(m))
    assert missing == [], f"{path.name} names modules that do not exist: {missing}"


def _bash_blocks(text: str) -> list[str]:
    return re.findall(r"```bash\n(.*?)```", text, re.S)


@pytest.mark.parametrize("path", [ROOT / "README.md", ROOT / "CLAUDE.md"], ids=lambda p: p.name)
def test_every_command_in_a_bash_block_refers_to_something_that_exists(path):
    problems = []
    for block in _bash_blocks(_text(path)):
        for line in block.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            try:
                words = shlex.split(line)
            except ValueError:
                continue
            for index, word in enumerate(words[:-1]):
                nxt = words[index + 1]
                if word == "-m" and index and words[index - 1].startswith("python"):
                    if importlib.util.find_spec(nxt.split(" ")[0]) is None:
                        problems.append(line)
                if word.startswith("python") and nxt.endswith(".py"):
                    if not (ROOT / nxt).is_file():
                        problems.append(line)
    assert problems == []


# ------------------------------------------------------------------ markers


def _ini_markers() -> set[str]:
    """Marker names ``pytest.ini`` declares."""
    parser = configparser.ConfigParser()
    parser.read(ROOT / "pytest.ini")
    lines = parser["pytest"]["markers"].strip().splitlines()
    return {re.split(r"[:(]", line.strip(), maxsplit=1)[0] for line in lines if line.strip()}


def _declared_markers() -> set[str]:
    return _ini_markers() | {lane.marker for lane in LANES}


_BUILTIN_MARKERS = {"parametrize", "skip", "skipif", "xfail", "usefixtures", "filterwarnings"}


def _registered_elsewhere() -> set[str]:
    """Markers repo code registers at runtime (``addinivalue_line("markers", ...)``)."""
    found = set()
    for path in ROOT.rglob("*.py"):
        if any(part in {".venv", "tests", "ext-config"} for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in re.finditer(r'addinivalue_line\(\s*"markers",\s*f?"([a-z_]+)', text):
            found.add(match.group(1))
        for match in re.finditer(r'addinivalue_line\(\s*"markers",\s*f"\{(\w+)\}', text):
            constant = re.search(rf'^{match.group(1)}\s*=\s*"([a-z_]+)"', text, re.M)
            if constant:
                found.add(constant.group(1))
    return found


def _marker_mentions(text: str) -> set[str]:
    mentions = set(re.findall(r"pytest\.mark\.([a-z_]+)", text))
    mentions |= set(re.findall(r"`@?([a-z_]+)(?:\([^`]*\))?` marker", text))
    return mentions


def test_markers_named_in_the_docs_are_declared_and_every_declared_marker_is_documented():
    docs = "\n".join(_text(p) for p in [*GUIDES, *FEATURES])
    declared = _declared_markers()
    known = declared | _registered_elsewhere() | _BUILTIN_MARKERS
    undeclared = sorted(_marker_mentions(docs) - known)
    assert undeclared == [], f"markers named in the docs but declared nowhere: {undeclared}"
    undocumented = sorted(m for m in declared if f"`{m}" not in docs and f".{m}" not in docs)
    assert undocumented == [], f"declared markers no doc mentions: {undocumented}"


# ------------------------------------------------------------------ environment variables


_PLATFORM = re.compile(
    r"^(GITHUB_\w+|RUNNER_\w+|CI|PATH|HOME|PYTHONPATH|PYTEST_ADDOPTS|TMPDIR|LANG|"
    r"ACTIONS_\w+|DOCKER_\w+)$"
)


@functools.lru_cache(maxsize=1)
def _code_text() -> str:
    chunks = []
    for pattern in ("**/*.py", ".github/workflows/*.yml", "docker-compose.yml", "Dockerfile*"):
        for path in ROOT.glob(pattern):
            if any(part in {".venv", "ext-config", "reports"} for part in path.parts):
                continue
            if path.is_file():
                chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(chunks)


@pytest.mark.parametrize("path", [*FEATURES, *NEW_DOCS], ids=lambda p: p.name)
def test_every_environment_variable_in_the_docs_appears_in_the_code(path):
    code = _code_text()
    names = {
        token.split("=", 1)[0]
        for token in _backticked(_text(path))
        if re.fullmatch(r"[A-Z][A-Z0-9]*_[A-Z0-9_]+(=[^`]*)?", token)
    }
    # A config key is read under its upper-case spelling (core.config_reader): `base_url`.
    missing = sorted(
        n for n in names if not _PLATFORM.match(n) and n not in code and n.lower() not in code
    )
    assert missing == [], f"{path.name} documents variables no code reads: {missing}"


# ------------------------------------------------------------------ what runs where


def _table(text: str, heading: str) -> list[list[str]]:
    section = text.split(heading, 1)[1].split("\n## ", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("| ") and "---" not in line]
    return [[cell.strip() for cell in row.strip("|").split("|")] for row in rows[1:]]


def _imported_by_tests(package: str) -> bool:
    pattern = re.compile(rf"^\s*(from|import)\s+{package}(\.|\s|$)", re.M)
    for base in ("tests", "ui/tests", "api/tests"):
        for path in (ROOT / base).rglob("*.py"):
            if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
                return True
    return False


def test_whats_live_packages_agree_with_the_tests():
    rows = _table(_text(ROOT / "docs" / "whats-live.md"), "## Packages")
    assert rows
    for package, status, *_ in rows:
        name = package.strip("`")
        assert (ROOT / name).is_dir(), f"{name} is not a package"
        assert status in {"default gate", "opt-in lane", "local-only", "experimental"}, status
        if status == "experimental":
            assert not _imported_by_tests(name), f"{name} is imported by a test"
        else:
            assert _imported_by_tests(name), f"{name} is marked {status} but no test imports it"


def test_whats_live_markers_agree_with_the_lane_policy():
    rows = _table(_text(ROOT / "docs" / "whats-live.md"), "## Markers and lanes")
    excluded = set(excluded_markers({}))
    workflow = _text(ROOT / ".github" / "workflows" / "tests.yml")
    lanes = {lane.marker: lane for lane in LANES}
    for marker, status, toggle in rows:
        name = marker.strip("`")
        assert name in _declared_markers(), name
        if status in {"default gate", "metadata"}:
            assert name not in excluded, f"{name} is excluded from the gate"
            assert status == "default gate" or name not in lanes, f"{name} is a lane"
        else:
            assert status == "opt-in lane", status
            if name in lanes:
                assert name in excluded, f"{name} is not excluded from the gate"
                if lanes[name].opt_in_env:
                    assert lanes[name].opt_in_env in toggle
            else:
                assert re.search(rf"if: .*lanes, '{name}'", workflow), name


# ------------------------------------------------------------------ the guide


def test_the_project_guide_stays_short():
    assert len(_text(ROOT / "CLAUDE.md").splitlines()) <= 200


# ------------------------------------------------------------------ CI decision links


def _anchor(heading: str) -> str:
    slug = re.sub(r"[^a-z0-9 -]", "", heading.lower()).strip()
    return slug.replace(" ", "-")


def test_every_ci_decision_is_linked_and_every_link_resolves():
    text = _text(ROOT / "docs" / "ci-decisions.md")
    anchors = {_anchor(h) for h in re.findall(r"^## (.+)$", text, re.M)}
    sources = [*WORKFLOWS, *[p for p in [*GUIDES, *FEATURES] if p.name != "ci-decisions.md"]]
    linked = set()
    for path in sources:
        linked |= set(re.findall(r"ci-decisions\.md#([a-z0-9-]+)", _text(path)))
    unlinked = sorted(anchors - linked)
    assert unlinked == [], f"ci-decisions sections nobody links: {unlinked}"
    for path in WORKFLOWS:
        broken = set(re.findall(r"ci-decisions\.md#([a-z0-9-]+)", _text(path))) - anchors
        assert broken == set(), f"{path.name} links missing sections: {sorted(broken)}"


def test_workflow_files_parse():
    for path in WORKFLOWS:
        assert isinstance(yaml.safe_load(_text(path)), dict)
