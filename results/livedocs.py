"""Living documentation: hand-written feature pages, rendered with the tests that prove them.

A page in ``docs/features/`` opts in with front matter and directives. Directives are
HTML comments, invisible when the raw Markdown is read on a code host::

    ---
    feature: billing
    requirements: [REQ-12, REQ-14]
    ---
    # Billing
    <!-- living:status -->
    <!-- living:cases feature=billing -->
    <!-- living:cases requirement=REQ-12 -->

``python -m results livedocs`` renders every living page to ``reports/living-docs/``
(``.md`` and ``.html``) and never writes back into ``docs/``. Each ``living:cases``
directive becomes a table (case, owner, latest outcome, last executed run and date,
revision, evidence); ``living:status`` becomes the page status:

* **failing** — a linked case failed in its latest sound run;
* **unproven** — no linked case, or not every linked case passed (skipped, not run);
  also when the page's requirements carry coverage verdicts and none says ``proved``;
* **stale** — everything passed, but the latest execution is older than
  ``LIVING_DOCS_STALE_DAYS`` (default 14);
* **proven** — every linked case passed in its latest sound run, recently.

A page whose cases all skipped is never shown as passing. Runs the integrity gate judged
FAIL are ignored. ``--check`` is the CI guard that keeps docs and tests linked: it fails on
a directive that matches no case, on a case carrying ``feature(x)`` that no page declares,
and on malformed front matter. It needs only the catalogue, no database.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from gate._env import env_int
from results.models import CaseRecord

STALE_ENV = "LIVING_DOCS_STALE_DAYS"
DEFAULT_STALE_DAYS = 14
DEFAULT_DOCS_DIR = Path("docs") / "features"
DEFAULT_OUT = Path("reports") / "living-docs"
_DIRECTIVE = re.compile(r"<!--\s*living:(\w+)((?:\s+[\w-]+=[^\s]+)*)\s*-->")
_ARG = re.compile(r"([\w-]+)=([^\s]+)")
STATUSES = ("proven", "failing", "unproven", "stale")


@dataclass(frozen=True)
class Directive:
    kind: str  # "cases" | "status"
    args: dict[str, str]
    line: int
    raw: str


@dataclass(frozen=True)
class LintIssue:
    page: str
    line: int
    code: str  # malformed-front-matter | unknown-directive | empty-directive | orphan-feature
    message: str

    def __str__(self) -> str:
        return f"{self.page}:{self.line}: {self.code}: {self.message}"


@dataclass
class DocPage:
    path: Path
    front: dict[str, Any]
    body: str  # the Markdown without front matter
    title: str
    directives: list[Directive] = field(default_factory=list)
    issues: list[LintIssue] = field(default_factory=list)
    body_offset: int = 0  # lines taken by the front matter

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def features(self) -> list[str]:
        out = _as_list(self.front.get("feature")) + _as_list(self.front.get("features"))
        out += [d.args["feature"] for d in self.directives if "feature" in d.args]
        return list(dict.fromkeys(out))

    @property
    def requirements(self) -> list[str]:
        out = _as_list(self.front.get("requirements")) + _as_list(self.front.get("requirement"))
        out += [d.args["requirement"] for d in self.directives if "requirement" in d.args]
        return list(dict.fromkeys(out))

    @property
    def is_living(self) -> bool:
        return bool(
            self.directives
            or self.front.get("feature")
            or self.front.get("features")
            or self.front.get("requirements")
        )


_FENCE = re.compile(r"^(```|~~~).*?^\1[^\n]*$", re.MULTILINE | re.DOTALL)


def _mask_code(text: str) -> str:
    """``text`` with fenced code blocks blanked (same length), so examples are not directives."""
    return _FENCE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def _directive_matches(body: str) -> list[re.Match[str]]:
    return list(_DIRECTIVE.finditer(_mask_code(body)))


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v is not None]
    return [str(value)]


def parse_page(path: str | Path) -> DocPage:
    """Front matter and directives of a page; problems become issues, never exceptions."""
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    front: dict[str, Any] = {}
    issues: list[LintIssue] = []
    body, offset = text, 0
    if text.startswith("---\n") or text.startswith("---\r\n"):
        end = re.search(r"^---\s*$", text[4:], re.MULTILINE)
        if end is None:
            issues.append(
                LintIssue(
                    path.name,
                    1,
                    "malformed-front-matter",
                    "front matter opened with --- but never closed",
                )
            )
        else:
            raw = text[4 : 4 + end.start()]
            offset = raw.count("\n") + 2
            body = text[4 + end.end() :].lstrip("\r\n")
            try:
                import yaml

                loaded = yaml.safe_load(raw) if raw.strip() else {}
                if loaded is None:
                    loaded = {}
                if not isinstance(loaded, dict):
                    raise ValueError("front matter must be a mapping")
                front = loaded
            except Exception as exc:
                issues.append(LintIssue(path.name, 1, "malformed-front-matter", str(exc)))
    directives = []
    for match in _directive_matches(body):
        line = body.count("\n", 0, match.start()) + 1 + offset
        kind = match.group(1)
        args = dict(_ARG.findall(match.group(2) or ""))
        if kind not in ("cases", "status"):
            issues.append(
                LintIssue(
                    path.name,
                    line,
                    "unknown-directive",
                    f"living:{kind} is not a directive (cases, status)",
                )
            )
            continue
        if kind == "cases" and not ({"feature", "requirement"} & set(args)):
            issues.append(
                LintIssue(
                    path.name,
                    line,
                    "unknown-directive",
                    "living:cases needs feature=<name> or requirement=<id>",
                )
            )
            continue
        directives.append(Directive(kind, args, line, match.group(0)))
    title_match = re.search(r"^#\s+(.+?)\s*$", body, re.MULTILINE)
    title = str(front.get("title") or (title_match.group(1) if title_match else path.stem))
    return DocPage(path, front, body, title, directives, issues, offset)


def load_pages(docs_dir: Path) -> list[DocPage]:
    if not Path(docs_dir).is_dir():
        return []
    return [parse_page(p) for p in sorted(Path(docs_dir).glob("*.md"))]


# ---------------------------------------------------------------------- lint


def check(docs_dir: Path, catalogue: Any) -> list[LintIssue]:
    """Docs <-> tests lint: empty directives, orphan feature markers, bad front matter."""
    pages = load_pages(docs_dir)
    issues: list[LintIssue] = []
    by_feature: dict[str, list[CaseRecord]] = {}
    by_requirement: dict[str, list[CaseRecord]] = {}
    for case in catalogue.cases:
        for kind, ref in case.links:
            if kind == "feature":
                by_feature.setdefault(ref, []).append(case)
            elif kind == "requirement":
                by_requirement.setdefault(ref, []).append(case)
    declared: set[str] = set()
    for page in pages:
        issues.extend(page.issues)
        declared.update(page.features)
        for d in page.directives:
            if d.kind != "cases":
                continue
            if "feature" in d.args and not by_feature.get(d.args["feature"]):
                issues.append(
                    LintIssue(
                        page.path.name,
                        d.line,
                        "empty-directive",
                        f"no test case carries feature({d.args['feature']!r})",
                    )
                )
            if "requirement" in d.args and not by_requirement.get(d.args["requirement"]):
                issues.append(
                    LintIssue(
                        page.path.name,
                        d.line,
                        "empty-directive",
                        f"no test case is linked to requirement {d.args['requirement']!r}",
                    )
                )
    for feature, cases in sorted(by_feature.items()):
        if feature not in declared:
            keys = ", ".join(c.case_key for c in cases[:3])
            issues.append(
                LintIssue(
                    "(tests)",
                    0,
                    "orphan-feature",
                    f"feature({feature!r}) on {keys} is declared by no page",
                )
            )
    return issues


# ---------------------------------------------------------------------- status


@dataclass(frozen=True)
class CaseLatest:
    case: CaseRecord
    outcome: str  # folded over the case's parameter rows; "never" when never executed
    run_id: str | None
    run_started_at: str | None
    revision_id: str | None
    evidence: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class PageStatus:
    status: str  # proven | failing | unproven | stale
    reasons: tuple[str, ...]
    cases: tuple[CaseLatest, ...]
    latest_execution: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reasons": list(self.reasons),
            "latest_execution": self.latest_execution,
            "cases": [
                {
                    "case_id": c.case.case_id,
                    "title": c.case.title or c.case.case_key,
                    "owner": c.case.owner,
                    "outcome": c.outcome,
                    "run_id": c.run_id,
                    "run_started_at": c.run_started_at,
                    "revision_id": c.revision_id,
                    "evidence": [e.get("uri") for e in c.evidence],
                }
                for c in self.cases
            ],
        }


def linked_cases(
    store: Any, *, feature: str | None = None, requirement: str | None = None
) -> list[CaseRecord]:
    from results.store import row_to_case

    out = []
    for row in store.query(
        "cases_list", feature=feature, requirement=requirement, status="active", limit=100_000
    ):
        links = [
            (r["kind"], r["ref"])
            for r in store.query(
                "case_links_for", case_id=row["case_id"], project_id=row["project_id"]
            )
        ]
        out.append(row_to_case(row, links))
    return out


def _page_cases(store: Any, page: DocPage, directive: Directive | None = None) -> list[CaseRecord]:
    found: dict[str, CaseRecord] = {}
    if directive is not None:
        targets = [(directive.args.get("feature"), directive.args.get("requirement"))]
    else:
        targets = [(f, None) for f in page.features] + [(None, r) for r in page.requirements]
    for feature, requirement in targets:
        for case in linked_cases(store, feature=feature, requirement=requirement):
            found.setdefault(case.case_id, case)
    return sorted(found.values(), key=lambda c: c.case_key)


def _latest(store: Any, case: CaseRecord) -> CaseLatest:
    from results.trends import fold_outcomes

    rows = store.query("case_history", case_id=case.case_id, project_id=case.project_id)
    by_run: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row["integrity_verdict"] == "FAIL":
            continue  # an unsound run is not evidence
        by_run.setdefault(row["run_id"], []).append(row)
    if not by_run:
        return CaseLatest(case, "never", None, None, None)
    run_id = list(by_run)[-1]  # case_history is ordered oldest run first
    rs = by_run[run_id]
    nodeids = {r["nodeid"] for r in rs}
    evidence = tuple(
        e for e in store.query("run_evidence", run_id=run_id) if e["nodeid"] in nodeids
    )
    return CaseLatest(
        case,
        fold_outcomes(r["outcome"] for r in rs),
        run_id,
        rs[0]["run_started_at"],
        rs[0]["revision_id"],
        evidence,
    )


def _coverage_proved(store: Any, requirements: Iterable[str]) -> bool | None:
    """True/False when the requirements carry coverage verdicts; None when none do."""
    seen = False
    for req in requirements:
        rows = store.conn.execute(
            "SELECT v.verdict, v.run_id FROM verdicts v JOIN runs r ON r.run_id = v.run_id"
            " WHERE v.kind = 'coverage' AND v.subject = ?"
            " AND (r.integrity_verdict IS NULL OR r.integrity_verdict <> 'FAIL')"
            " ORDER BY r.started_at DESC, v.recorded_at DESC",
            (req,),
        ).fetchall()
        if not rows:
            continue
        seen = True
        latest_run = rows[0][1]
        if any(v == "proved" for v, run in rows if run == latest_run):
            return True
    return False if seen else None


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def page_status(
    store: Any, page: DocPage, *, now: datetime | None = None, stale_days: int | None = None
) -> PageStatus:
    """The page's status from its linked cases' latest sound results (see module doc)."""
    days = env_int(STALE_ENV, DEFAULT_STALE_DAYS) if stale_days is None else stale_days
    now = now or datetime.now(tz=timezone.utc)
    latest = tuple(_latest(store, c) for c in _page_cases(store, page))
    executed = [c for c in latest if c.run_started_at]
    newest = max((c.run_started_at for c in executed), default=None)
    reasons: list[str] = []
    if not latest:
        return PageStatus("unproven", ("no test case is linked to this page",), latest, None)
    failing = [c for c in latest if c.outcome in ("failed", "error")]
    if failing:
        reasons = [f"{c.case.title or c.case.case_key}: {c.outcome}" for c in failing]
        return PageStatus("failing", tuple(reasons), latest, newest)
    not_passed = [c for c in latest if c.outcome != "passed"]
    if not_passed:
        reasons = [
            f"{c.case.title or c.case.case_key}: "
            f"{'never executed' if c.outcome == 'never' else c.outcome}"
            for c in not_passed
        ]
        return PageStatus("unproven", tuple(reasons), latest, newest)
    proved = _coverage_proved(store, page.requirements)
    if proved is False:
        return PageStatus(
            "unproven",
            ("the requirements' coverage verdicts include no 'proved'",),
            latest,
            newest,
        )
    times = [t for t in (_parse_time(c.run_started_at) for c in latest) if t is not None]
    oldest_latest = min(times) if times else None
    if oldest_latest is not None and now - oldest_latest > timedelta(days=days):
        return PageStatus(
            "stale",
            (f"a linked case last ran {oldest_latest:%Y-%m-%d}, more than {days} days ago",),
            latest,
            newest,
        )
    return PageStatus("proven", (f"all {len(latest)} linked case(s) passed",), latest, newest)


# ---------------------------------------------------------------------- rendering


def _cases_table_md(rows: list[CaseLatest]) -> str:
    if not rows:
        return "_No test case is linked here: unproven._\n"
    lines = [
        "| Case | Owner | Latest outcome | Last run | Run date | Revision | Evidence |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in rows:
        evidence = ", ".join(f"{e['kind']}: {e['uri']}" for e in c.evidence) or "—"
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} |".format(
                (c.case.title or c.case.case_key).replace("|", "\\|"),
                c.case.owner or "—",
                "never executed" if c.outcome == "never" else c.outcome,
                c.run_id or "—",
                (c.run_started_at or "—")[:10],
                (c.revision_id or "—")[:12],
                evidence.replace("|", "\\|"),
            )
        )
    return "\n".join(lines) + "\n"


def _status_md(status: PageStatus) -> str:
    reasons = "; ".join(status.reasons)
    return f"**Status: {status.status.upper()}** — {reasons}\n"


def render_page(
    store: Any, page: DocPage, *, fmt: Literal["md", "html"] = "md", now: datetime | None = None
) -> str:
    """The page with every directive replaced by live data."""
    status = page_status(store, page, now=now)

    def replace(match: re.Match[str]) -> str:
        kind = match.group(1)
        args = dict(_ARG.findall(match.group(2) or ""))
        if kind == "status":
            return _status_md(status)
        if kind == "cases":
            directive = Directive("cases", args, 0, match.group(0))
            rows = [_latest(store, c) for c in _page_cases(store, page, directive)]
            return _cases_table_md(rows)
        return match.group(0)

    parts, last = [], 0
    for match in _directive_matches(page.body):
        parts += [page.body[last : match.start()], replace(match)]
        last = match.end()
    md = "".join(parts) + page.body[last:]
    if not any(d.kind == "status" for d in page.directives):
        md = _status_md(status) + "\n" + md
    if fmt == "md":
        return md
    return markdown_to_html(md, title=page.title, status=status.status)


_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def _inline(text: str) -> str:
    out = html_lib.escape(text, quote=False)
    out = _INLINE_CODE.sub(lambda m: f"<code>{m.group(1)}</code>", out)
    out = _BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", out)

    def link(m: re.Match[str]) -> str:
        href = m.group(2)
        if re.match(r"^[a-z]+:", href) or href.startswith("//"):
            return m.group(1)  # no external links in a self-contained artefact
        if href.endswith(".md"):
            href = href[:-3] + ".html"
        return f'<a href="{html_lib.escape(href)}">{m.group(1)}</a>'

    return _LINK.sub(link, out)


def markdown_to_html(md: str, *, title: str, status: str = "") -> str:
    """A small, dependency-free Markdown subset: headings, lists, tables, code, paragraphs."""
    out: list[str] = []
    lines = md.splitlines()
    i = 0
    para: list[str] = []

    def flush() -> None:
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            flush()
            block = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            out.append("<pre><code>" + html_lib.escape("\n".join(block)) + "</code></pre>")
            i += 1
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            flush()
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            i += 1
            continue
        if (
            stripped.startswith("|")
            and i + 1 < len(lines)
            and re.match(r"^\|?\s*:?-+", lines[i + 1].strip())
        ):
            flush()
            header = [c.strip() for c in stripped.strip("|").split("|")]
            rows = []
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = re.split(r"(?<!\\)\|", lines[i].strip().strip("|"))
                rows.append([c.strip().replace("\\|", "|") for c in cells])
                i += 1
            head = "".join(f"<th>{_inline(h)}</th>" for h in header)
            body = "".join(
                "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in rows
            )
            out.append(
                f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead>'
                f"<tbody>{body}</tbody></table></div>"
            )
            continue
        bullet = re.match(r"^\s*([-*]|\d+[.)])\s+(.*)$", line)
        if bullet:
            flush()
            ordered = bullet.group(1)[0].isdigit()
            tag = "ol" if ordered else "ul"
            items = []
            while i < len(lines):
                m = re.match(r"^\s*([-*]|\d+[.)])\s+(.*)$", lines[i])
                if not m:
                    if lines[i].startswith("  ") and lines[i].strip() and items:
                        items[-1] += " " + lines[i].strip()
                        i += 1
                        continue
                    break
                items.append(m.group(2))
                i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + f"</{tag}>")
            continue
        if re.match(r"^<!--.*-->$", stripped):
            i += 1
            continue
        if not stripped:
            flush()
        else:
            para.append(stripped)
        i += 1
    flush()
    from results.dashboard import CSS

    badge = f'<p><span class="pill {html_lib.escape(status)}">{html_lib.escape(status)}</span></p>'
    extra_css = ".proven{color:var(--pass)}.stale{color:var(--warn)}.unproven{color:var(--skip)}"
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html_lib.escape(title)}</title><style>{CSS}{extra_css}</style></head>"
        f'<body><nav><a href="index.html">Living docs</a></nav><main>{badge}'
        + "\n".join(out)
        + "</main></body></html>\n"
    )


def build(
    store: Any,
    docs_dir: Path = DEFAULT_DOCS_DIR,
    out_dir: Path = DEFAULT_OUT,
    *,
    now: datetime | None = None,
) -> list[Path]:
    """Render every living page (``.md`` and ``.html``) plus an index; returns files written."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    index_rows = []
    for page in load_pages(docs_dir):
        if not page.is_living:
            continue
        status = page_status(store, page, now=now)
        md = render_page(store, page, fmt="md", now=now)
        (out_dir / f"{page.name}.md").write_text(md, encoding="utf-8")
        html = render_page(store, page, fmt="html", now=now)
        (out_dir / f"{page.name}.html").write_text(html, encoding="utf-8")
        written += [out_dir / f"{page.name}.md", out_dir / f"{page.name}.html"]
        index_rows.append((page, status))
    lines = ["# Living documentation", "", "| Page | Status | Why |", "|---|---|---|"]
    for page, status in index_rows:
        lines.append(
            f"| [{page.title}]({page.name}.md) | {status.status} | "
            f"{'; '.join(status.reasons).replace('|', '/')} |"
        )
    if not index_rows:
        lines.append("| — | — | no page declares a feature or carries a living directive |")
    index_md = "\n".join(lines) + "\n"
    (out_dir / "index.md").write_text(index_md, encoding="utf-8")
    (out_dir / "index.html").write_text(
        markdown_to_html(index_md, title="Living documentation"), encoding="utf-8"
    )
    (out_dir / "status.json").write_text(
        json.dumps({p.name: s.to_json() for p, s in index_rows}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    written += [out_dir / "index.md", out_dir / "index.html", out_dir / "status.json"]
    return written


def page_summaries(store: Any, docs_dir: Path = DEFAULT_DOCS_DIR) -> list[dict[str, Any]]:
    return [
        {"page": p.name, "title": p.title, **page_status(store, p).to_json()}
        for p in load_pages(docs_dir)
        if p.is_living
    ]


__all__ = [
    "DocPage",
    "Directive",
    "LintIssue",
    "PageStatus",
    "build",
    "check",
    "linked_cases",
    "markdown_to_html",
    "page_status",
    "page_summaries",
    "parse_page",
    "render_page",
]
