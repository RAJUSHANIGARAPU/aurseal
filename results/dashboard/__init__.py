"""Static HTML dashboard rendered from the results store.

``python -m results dashboard --out reports/dashboard/`` writes a self-contained
directory: inline CSS, inline SVG charts, a few lines of vanilla JS for filtering, no
external fetches, no framework. It reads only through :mod:`results.trends` and
:mod:`results.catalogue` (plus the store's named queries), so a Grafana or other front end
can sit on the same queries later.

Every number carries its denominator ("pass 92% (46/50 executed, 12 skipped)"), and a run
or case that executed nothing reads *unmeasured*, never a percentage.

Pages: ``index.html`` (overview), ``runs/<run>.html``, ``cases/<case>.html``,
``unstable.html``, ``slowest.html``, ``catalogue.html``, ``search.html``.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Sequence
from html import escape
from pathlib import Path
from string import Template
from typing import Any

from results.dashboard.svg import line_chart, timeline

CSS = """
:root{--bg:#fbfbfa;--fg:#1d1d1b;--muted:#6b6b66;--line:#e2e1dc;--card:#ffffff;
--pass:#2f7d4f;--fail:#b3261e;--skip:#8a8a84;--warn:#a15c00;--accent:#2b5c9e}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ecebe7;--muted:#a3a29c;
--line:#33332f;--card:#1f1f1d;--pass:#5fbf86;--fail:#ef7a72;--skip:#8f8e88;--warn:#e0a24f;
--accent:#7fa8e0}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px}
nav{display:flex;flex-wrap:wrap;gap:12px;padding:10px 16px;border-bottom:1px solid var(--line)}
nav a{color:var(--accent);text-decoration:none}h1{font-size:22px;margin:8px 0 4px}
h2{font-size:16px;margin:24px 0 8px}.muted{color:var(--muted)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px}
.card .big{font-size:20px;font-weight:600}
.table-wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600}code{font-size:12px;word-break:break-all}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;
border:1px solid currentColor}
.passed,.PASS{color:var(--pass)}.failed,.error,.FAIL{color:var(--fail)}
.skipped,.not_run,.xfailed,.UNJUDGED,.unmeasured-text{color:var(--skip)}
.chart,.timeline{width:100%;max-width:100%;height:auto}.timeline{max-height:40px}
.grid{stroke:var(--line)}.axis{fill:var(--muted);font-size:10px}
.series{stroke:var(--accent);stroke-width:2}.point{fill:var(--accent)}
.unmeasured{fill:none;stroke:var(--skip);stroke-width:1.5}
.o-passed{fill:var(--pass)}.o-failed,.o-error{fill:var(--fail)}
.o-skipped,.o-not_run,.o-xfailed{fill:var(--skip)}
.boundary{stroke:var(--warn);stroke-dasharray:3 2}
input[type=search]{width:100%;max-width:420px;padding:6px 8px;border:1px solid var(--line);
border-radius:6px;background:var(--card);color:var(--fg)}
"""

PAGE = Template(
    """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$title</title><style>$css</style></head>
<body><nav>$nav</nav><main>
<h1>$heading</h1>
$body
<p class="muted">Generated $generated from the results store.</p>
</main>$script</body></html>
"""
)

NAV = (
    ("index.html", "Overview"),
    ("unstable.html", "Unstable"),
    ("slowest.html", "Slowest"),
    ("catalogue.html", "Catalogue"),
    ("search.html", "Search"),
)

CATALOGUE_JS = """<script>
(function(){var q=document.getElementById('filter');var rows=document.querySelectorAll(
'#cases tbody tr');var count=document.getElementById('count');function apply(){
var t=q.value.toLowerCase().trim();var n=0;rows.forEach(function(r){
var hit=!t||r.textContent.toLowerCase().indexOf(t)>=0;r.hidden=!hit;if(hit)n++;});
count.textContent=n+' of '+rows.length+' case(s)';}q.addEventListener('input',apply);apply();
})();
</script>"""

SEARCH_JS = """<script>
(function(){var box=document.getElementById('q');var out=document.getElementById('hits');
var note=document.getElementById('note');var docs=null;
var raw=document.getElementById('search-index');
try{docs=JSON.parse(raw.textContent).docs;}catch(e){docs=null;}
if(!docs||!docs.length){note.textContent='No search index was built for this dashboard.';
box.disabled=true;return;}note.textContent=docs.length+' searchable item(s).';
function esc(s){var d=document.createElement('div');d.textContent=s;return d.innerHTML;}
function run(){var terms=box.value.toLowerCase().split(/\\s+/).filter(Boolean);out.innerHTML='';
if(!terms.length)return;var shown=0;for(var i=0;i<docs.length&&shown<100;i++){
var d=docs[i];var text=(d.title+' '+d.body).toLowerCase();
if(terms.every(function(t){return text.indexOf(t)>=0;})){shown++;
var li=document.createElement('li');var label='<span class="pill">'+esc(d.doc_type)+'</span> ';
li.innerHTML=label+(d.href?'<a href="'+esc(d.href)+'">'+esc(d.title)+'</a>':esc(d.title))+
'<div class="muted">'+esc(d.body.slice(0,200))+'</div>';out.appendChild(li);}}
if(!shown)out.innerHTML='<li class="muted">No match.</li>';}
box.addEventListener('input',run);})();
</script>"""


def safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "item"


def _nav(prefix: str) -> str:
    return " ".join(f'<a href="{prefix}{href}">{escape(label)}</a>' for href, label in NAV)


def _page(title: str, body: str, *, prefix: str = "", generated: str = "", script: str = "") -> str:
    return PAGE.substitute(
        title=escape(title),
        heading=escape(title),
        css=CSS,
        nav=_nav(prefix),
        body=body,
        generated=escape(generated),
        script=script,
    )


def _pill(text: str | None) -> str:
    value = text or "—"
    return f'<span class="pill {escape(value)}">{escape(value)}</span>'


def _table(headers: Sequence[str], rows: Iterable[Sequence[str]], table_id: str = "") -> str:
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    attr = f' id="{table_id}"' if table_id else ""
    return (
        f'<div class="table-wrap"><table{attr}><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def _pct(value: float | None) -> float | None:
    return None if value is None else round(100.0 * value, 1)


class _Renderer:
    def __init__(self, store: Any, out_dir: Path, window: int, base_artifact_url: str | None):
        self.store = store
        self.out = Path(out_dir)
        self.window = window
        self.base = base_artifact_url.rstrip("/") + "/" if base_artifact_url else None
        from results._util import ARTIFACT_ROOT_ENV, utc_now

        self.artifact_root = Path(os.environ.get(ARTIFACT_ROOT_ENV) or Path.cwd()).resolve()
        self.generated = utc_now()
        self.written: list[Path] = []
        self.case_pages: set[str] = set()
        self.run_pages: set[str] = set()

    def write(self, rel: str, html: str) -> None:
        path = self.out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        self.written.append(path)

    def evidence_href(self, uri: str, page_dir: Path) -> str:
        if uri.startswith(("http://", "https://")):
            if self.base and uri.startswith(self.base):
                return uri
            return ""  # an external link would leave the artefact: shown as text only
        if self.base:
            return self.base + uri.lstrip("/")
        target = Path(uri) if Path(uri).is_absolute() else self.artifact_root / uri
        return Path(os.path.relpath(target, page_dir)).as_posix()

    # ---------------------------------------------------------------- pages

    def overview(self, points: list[Any]) -> None:
        from results.trends import rate_text

        body: list[str] = []
        if not points:
            body.append('<p class="unmeasured-text">No runs stored: nothing has been measured.</p>')
        else:
            last = points[-1]
            ratio = last.executed_ratio
            denominator = max(last.selected, last.expected_count or 0)
            ratio_text = (
                "unmeasured"
                if ratio is None
                else f"{100 * ratio:.0f}% ({last.executed}/{denominator})"
            )
            body.append('<div class="cards">')
            body.append(
                f'<div class="card"><div class="muted">Last run</div><div class="big">'
                f'<a href="runs/{safe(last.run_id)}.html">{escape(last.run_id)}</a></div>'
                f'<div class="muted">{escape(last.started_at or "")}</div></div>'
            )
            body.append(
                f'<div class="card"><div class="muted">Pass rate</div>'
                f'<div class="big">{escape(last.rate_text)}</div></div>'
            )
            body.append(
                f'<div class="card"><div class="muted">Executed of selected</div>'
                f'<div class="big">{escape(ratio_text)}</div></div>'
            )
            body.append(
                f'<div class="card"><div class="muted">Integrity verdict</div>'
                f'<div class="big">{_pill(last.integrity_verdict or "not judged")}</div></div>'
            )
            body.append("</div>")
            body.append("<h2>Pass rate per run</h2>")
            body.append(
                line_chart(
                    [(p.run_id, _pct(p.pass_rate)) for p in points],
                    y_label="pass rate of executed tests",
                    denominators=[p.rate_text for p in points],
                )
            )
            body.append("<h2>Executed share of selected tests</h2>")
            body.append(
                line_chart(
                    [(p.run_id, _pct(p.executed_ratio)) for p in points],
                    y_label="executed / selected",
                    denominators=[f"{p.executed}/{p.selected} executed" for p in points],
                )
            )
            lanes: dict[str, list[Any]] = {}
            for p in points:
                lanes.setdefault(p.lane or "(none)", []).append(p)
            body.append("<h2>Lanes</h2>")
            body.append(
                _table(
                    ["Lane", "Runs", "Latest run", "Latest pass rate", "Integrity"],
                    [
                        [
                            escape(lane),
                            str(len(ps)),
                            f'<a href="runs/{safe(ps[-1].run_id)}.html">'
                            f"{escape(ps[-1].run_id)}</a>",
                            escape(ps[-1].rate_text),
                            _pill(ps[-1].integrity_verdict or "not judged"),
                        ]
                        for lane, ps in sorted(lanes.items())
                    ],
                )
            )
            body.append("<h2>Recent runs</h2>")
            body.append(
                _table(
                    ["Run", "Started", "Lane", "Branch", "Result", "Retried passes", "Integrity"],
                    [
                        [
                            f'<a href="runs/{safe(p.run_id)}.html">{escape(p.run_id)}</a>',
                            escape(p.started_at or ""),
                            escape(p.lane or ""),
                            escape(p.branch or ""),
                            escape(rate_text(p.passed, p.executed, p.skipped, p.not_run)),
                            str(p.passed_on_retry),
                            _pill(p.integrity_verdict or "not judged"),
                        ]
                        for p in reversed(points)
                    ],
                )
            )
        self.write("index.html", _page("Test results", "".join(body), generated=self.generated))

    def run_page(self, run_id: str) -> None:
        from results.store import row_to_run
        from results.trends import compare_runs, rate_text

        page_dir = (self.out / "runs").resolve()
        run = row_to_run(self.store.query("run_get", run_id=run_id)[0])
        finals = self.store.query("run_final_results", run_id=run_id)
        counts: dict[str, int] = {}
        for r in finals:
            counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
        failed = counts.get("failed", 0) + counts.get("error", 0)
        executed = counts.get("passed", 0) + failed
        headline = rate_text(
            counts.get("passed", 0), executed, counts.get("skipped", 0), counts.get("not_run", 0)
        )
        body = [
            '<div class="cards">',
            f'<div class="card"><div class="muted">Result</div><div class="big">'
            f"{escape(headline)}</div></div>",
            f'<div class="card"><div class="muted">Integrity verdict</div><div class="big">'
            f"{_pill(run.integrity_verdict or 'not judged')}</div></div>",
            f'<div class="card"><div class="muted">Status</div><div class="big">'
            f"{escape(run.status)}</div></div>",
            "</div>",
            _table(
                ["Field", "Value"],
                [
                    [escape(k), escape(str(v))]
                    for k, v in (
                        ("lane", run.lane),
                        ("branch", run.git_branch),
                        ("commit", run.git_sha),
                        ("environment", run.environment),
                        ("started", run.started_at),
                        ("finished", run.finished_at),
                        ("release", run.release_label),
                        ("expected tests", run.expected_count),
                    )
                    if v is not None
                ],
            ),
            "<h2>Outcomes</h2>",
            _table(
                ["Outcome", "Tests"],
                [
                    [_pill(o), str(counts.get(o, 0))]
                    for o in ("passed", "failed", "error", "xfailed", "skipped", "not_run")
                ],
            ),
        ]
        if run.ci_url:
            body.insert(0, f'<p class="muted">CI: {escape(run.ci_url)}</p>')
        cmp = compare_runs(self.store, run_id)
        body.append("<h2>Compared with the previous run</h2>")
        if cmp.baseline_run_id is None:
            body.append('<p class="muted">No baseline run stored: nothing to compare against.</p>')
        else:
            body.append(
                f'<p>Baseline: <a href="{safe(cmp.baseline_run_id)}.html">'
                f"{escape(cmp.baseline_run_id)}</a></p>"
            )
            body.append(
                _table(
                    ["Bucket", "Tests"],
                    [
                        [
                            escape(name.replace("_", " ")),
                            escape(", ".join(getattr(cmp, name)) or "—"),
                        ]
                        for name in cmp.BUCKETS
                    ],
                )
            )
        evidence: dict[str, list[dict[str, Any]]] = {}
        for ev in self.store.query("run_evidence", run_id=run_id):
            evidence.setdefault(ev["nodeid"], []).append(ev)
        failures = [r for r in finals if r["outcome"] in ("failed", "error")]
        body.append(f"<h2>Failures ({len(failures)})</h2>")
        if failures:
            body.append(
                _table(
                    ["Test", "Outcome", "Message", "Fingerprint", "Evidence"],
                    [
                        [
                            self.case_link(r["case_id"], r["nodeid"], prefix="../"),
                            _pill(r["outcome"]),
                            escape((r["message"] or "")[:400]),
                            f"<code>{escape(r['failure_fingerprint'] or '')}</code>",
                            self.evidence_links(evidence.get(r["nodeid"], []), page_dir),
                        ]
                        for r in failures
                    ],
                )
            )
        run_level = evidence.get("", [])
        if run_level:
            body.append("<h2>Run evidence</h2>")
            body.append(f"<p>{self.evidence_links(run_level, page_dir)}</p>")
        body.append(f"<h2>All tests ({len(finals)})</h2>")
        body.append(
            _table(
                ["Test", "Outcome", "Attempts", "Duration (ms)", "Worker"],
                [
                    [
                        self.case_link(r["case_id"], r["nodeid"], prefix="../"),
                        _pill(r["outcome"]),
                        str(r["attempts"]),
                        str(r["duration_ms"] if r["duration_ms"] is not None else ""),
                        escape(r["worker"] or ""),
                    ]
                    for r in finals
                ],
            )
        )
        body.append(
            f'<p class="muted">Bundle: <code>python -m results export bundle --run-id '
            f"{escape(run_id)}</code> · results CSV/JSON: <code>python -m results export "
            f"results --run-id {escape(run_id)} --format csv</code> · tracker payloads: "
            f"<code>python -m results export tms xray --run-id {escape(run_id)}</code></p>"
        )
        self.write(
            f"runs/{safe(run_id)}.html",
            _page(f"Run {run_id}", "".join(body), prefix="../", generated=self.generated),
        )

    def case_link(self, case_id: str | None, nodeid: str, *, prefix: str = "") -> str:
        label = f"<code>{escape(nodeid)}</code>"
        if case_id and case_id in self.case_pages:
            return f'<a href="{prefix}cases/{safe(case_id)}.html">{label}</a>'
        return label

    def evidence_links(self, rows: list[dict[str, Any]], page_dir: Path) -> str:
        parts = []
        for ev in rows:
            href = self.evidence_href(ev["uri"], page_dir)
            label = escape(ev["kind"])
            parts.append(
                f'<a href="{escape(href)}">{label}</a>'
                if href
                else f"{label}: <code>{escape(ev['uri'])}</code>"
            )
        return " ".join(parts) or "—"

    def case_page(self, case: Any) -> None:
        from results.trends import case_history, trend_from_points

        points = case_history(self.store, case.case_id, limit=0, project_id=case.project_id)
        trend = trend_from_points(case.case_id, points, window=self.window)
        recent = points[-self.window :] if self.window else points
        links = ", ".join(f"{escape(k)}: {escape(r)}" for k, r in case.links) or "—"
        steps = (
            "<ol>" + "".join(f"<li>{escape(s)}</li>" for s in case.steps) + "</ol>"
            if case.steps
            else '<p class="muted">No steps recorded.</p>'
        )
        body = [
            _table(
                ["Field", "Value"],
                [
                    ["case id", f"<code>{escape(case.case_id)}</code>"],
                    ["key", f"<code>{escape(case.case_key)}</code>"],
                    ["status", escape(case.status)],
                    ["owner", escape(case.owner or "—")],
                    ["priority", escape(case.priority or "—")],
                    ["links", links],
                    ["revision", f"<code>{escape(case.current_revision_id or '—')}</code>"],
                ],
            ),
        ]
        if case.description:
            body.append(f"<p>{escape(case.description)}</p>")
        body.append("<h2>Steps</h2>")
        body.append(steps)
        body.append("<h2>Trend</h2>")
        drift = (
            "—"
            if trend.duration_drift is None
            else f"{100 * trend.duration_drift:+.0f}%"
            + (" (flagged)" if trend.drift_flagged else "")
        )
        streak = f"{trend.streak_length} × {trend.streak_outcome}" if trend.streak_outcome else "—"
        body.append(
            _table(
                ["Measure", "Value"],
                [
                    ["pass rate", escape(trend.rate_text)],
                    ["flips", str(trend.flips)],
                    ["streak", escape(streak)],
                    ["failing since", escape(trend.failing_since_run_id or "—")],
                    ["passed only after a retry", str(trend.passed_after_retry)],
                    [
                        "duration p50 / p95 (ms)",
                        escape(f"{trend.duration_p50_ms or '—'} / {trend.duration_p95_ms or '—'}"),
                    ],
                    ["duration drift", escape(drift)],
                    ["unsound runs left out", str(trend.excluded_unsound)],
                ],
            )
        )
        body.append("<h2>Outcome per run</h2>")
        if recent:
            body.append(
                timeline([(p.run_id, p.outcome) for p in recent], set(trend.revision_boundaries))
            )
            body.append('<p class="muted">Dashed line: the test\'s revision changed.</p>')
            body.append("<h2>Duration</h2>")
            durations = [p.duration_ms for p in recent if p.duration_ms is not None]
            top = float(max(durations)) if durations else 1.0
            body.append(
                line_chart(
                    [
                        (p.run_id, None if p.duration_ms is None else float(p.duration_ms))
                        for p in recent
                    ],
                    y_label="duration (ms)",
                    denominators=[p.outcome for p in recent],
                    y_max=top,
                    unit="",
                )
            )
            body.append(
                _table(
                    ["Run", "Started", "Outcome", "Revision", "Message"],
                    [
                        [
                            f'<a href="../runs/{safe(p.run_id)}.html">{escape(p.run_id)}</a>'
                            if p.run_id in self.run_pages
                            else escape(p.run_id),
                            escape(p.started_at or ""),
                            _pill(p.outcome),
                            f"<code>{escape((p.revision_id or '')[:12])}</code>",
                            escape((p.message or "")[:200]),
                        ]
                        for p in reversed(recent)
                    ],
                )
            )
        else:
            body.append('<p class="unmeasured-text">Never executed: unmeasured.</p>')
        self.write(
            f"cases/{safe(case.case_id)}.html",
            _page(
                case.title or case.case_key, "".join(body), prefix="../", generated=self.generated
            ),
        )

    def top_lists(self) -> None:
        from results.trends import slowest, unstable

        rows = unstable(self.store, window=self.window)
        body = [
            f'<p class="muted">Cases that flipped between pass and fail at least twice, or '
            f"passed only after a retry, in the last {self.window} sound runs.</p>",
            _table(
                ["Case", "Flips", "Passed after retry", "Pass rate"],
                [
                    [
                        self.case_link(r["case_id"], r["title"]),
                        str(r["flips"]),
                        str(r["passed_after_retry"]),
                        escape(r["rate_text"]),
                    ]
                    for r in rows
                ],
            )
            if rows
            else '<p class="muted">No unstable case in the window.</p>',
        ]
        self.write(
            "unstable.html", _page("Unstable cases", "".join(body), generated=self.generated)
        )
        rows = slowest(self.store, window=self.window)
        body = [
            f'<p class="muted">Median duration over the last {self.window} sound runs.</p>',
            _table(
                ["Case", "p50 (ms)", "p95 (ms)", "Samples"],
                [
                    [
                        self.case_link(r["case_id"], r["title"]),
                        str(r["p50_ms"]),
                        str(r["p95_ms"]),
                        str(r["samples"]),
                    ]
                    for r in rows
                ],
            )
            if rows
            else '<p class="muted">No durations recorded.</p>',
        ]
        self.write("slowest.html", _page("Slowest cases", "".join(body), generated=self.generated))

    def catalogue_page(self, cases: list[Any]) -> None:
        rows = [
            [
                self.case_link(c.case_id, c.title or c.case_key),
                f"<code>{escape(c.case_key)}</code>",
                escape(c.owner or ""),
                escape(c.priority or ""),
                escape(", ".join(m.get("name", "") for m in c.markers)),
                escape(", ".join(f"{k}:{r}" for k, r in c.links)),
                escape(c.status),
            ]
            for c in cases
        ]
        body = (
            '<p><input type="search" id="filter" placeholder="Filter cases" '
            'aria-label="Filter cases"> <span id="count" class="muted"></span></p>'
            + _table(
                ["Case", "Key", "Owner", "Priority", "Markers", "Links", "Status"], rows, "cases"
            )
        )
        self.write(
            "catalogue.html",
            _page("Test case catalogue", body, generated=self.generated, script=CATALOGUE_JS),
        )

    def search_page(self, index: dict[str, Any] | None) -> None:
        data = json.dumps(index or {"docs": []}, ensure_ascii=False).replace("</", "<\\/")
        body = (
            '<p><input type="search" id="q" placeholder="Search cases, failures, docs" '
            'aria-label="Search"></p><p id="note" class="muted"></p><ul id="hits"></ul>'
            f'<script type="application/json" id="search-index">{data}</script>'
        )
        self.write("search.html", _page("Search", body, generated=self.generated, script=SEARCH_JS))


def render(
    store: Any,
    out_dir: Path,
    *,
    window: int = 30,
    base_artifact_url: str | None = None,
    search_index: dict[str, Any] | None = None,
) -> list[Path]:
    """Render every page into ``out_dir``; returns the files written."""
    from results.catalogue import catalogue_from_store
    from results.trends import suite_trend

    r = _Renderer(store, Path(out_dir), window, base_artifact_url)
    points = suite_trend(store, window=window)
    r.run_pages = {p.run_id for p in points}
    cases = list(catalogue_from_store(store).cases)
    r.case_pages = {c.case_id for c in cases}
    r.overview(points)
    for p in points:
        r.run_page(p.run_id)
    for case in cases:
        r.case_page(case)
    r.top_lists()
    r.catalogue_page(cases)
    if search_index is None:
        try:
            from results.search import index_payload

            search_index = index_payload(store)
        except Exception:
            search_index = None  # the page then says no index was built
    r.search_page(search_index)
    if search_index is not None:
        target = Path(out_dir) / "search-index.json"
        target.write_text(json.dumps(search_index, ensure_ascii=False), encoding="utf-8")
        r.written.append(target)
    return r.written


__all__ = ["render", "safe"]
