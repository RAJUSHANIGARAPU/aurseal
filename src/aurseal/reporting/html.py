"""Static HTML report: one self-contained file, no scripts, every text escaped.

The page is deterministic for a given run and timestamp (records are sorted, counts are
computed, no randomness), so it can be compared byte for byte in tests.
"""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path

from aurseal import __version__
from aurseal.model.v1 import RunUpload, Status, TestRecord

MESSAGE_LIMIT = 500
_ORDER = {
    Status.FAILED: 0,
    Status.BROKEN: 1,
    Status.BLOCKED: 2,
    Status.RETEST: 3,
    Status.SKIPPED: 4,
    Status.PASSED: 5,
}

_CSS = """
:root{--bg:#fff;--fg:#1d1d1f;--muted:#5f6368;--line:#e2e2e6;--pass:#17733a;--fail:#b3261e;
--broken:#8a4b00;--skip:#5f6368;--chip:#f3f3f6}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--fg:#ececf0;--muted:#a0a0aa;
--line:#2c2c31;--pass:#5bd28a;--fail:#ff8a80;--broken:#ffb74d;--skip:#a0a0aa;--chip:#222227}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px}
h1{font-size:22px;margin:0 0 4px}
.meta{color:var(--muted);margin:0 0 20px}
.tiles{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 20px;padding:0;list-style:none}
.tiles li{background:var(--chip);border-radius:8px;padding:8px 12px;min-width:96px}
.tiles b{display:block;font-size:20px}
.table{overflow-x:auto}
table{border-collapse:collapse;width:100%}
th,td{text-align:left;vertical-align:top;padding:6px 8px;border-bottom:1px solid var(--line)}
th{font-weight:600;color:var(--muted)}
td.msg{font-family:ui-monospace,Menlo,monospace;font-size:12px;white-space:pre-wrap;
word-break:break-word;max-width:420px}
.s{font-weight:600}.s-passed{color:var(--pass)}.s-failed{color:var(--fail)}
.s-broken{color:var(--broken)}.s-skipped,.s-blocked,.s-retest{color:var(--skip)}
.flaky{font-size:12px;color:var(--broken)}
"""


def _e(value: object) -> str:
    return escape(str(value), quote=True)


def _sort_key(record: TestRecord) -> tuple[int, str, str, str, str, str]:
    return (
        _ORDER[record.status],
        record.path,
        record.suite,
        record.name,
        record.variant or "",
        record.configuration or "",
    )


def _duration(ms: int | None) -> str:
    if ms is None:
        return ""
    return f"{ms} ms" if ms < 1000 else f"{ms / 1000:.2f} s"


def _row(record: TestRecord) -> str:
    title = record.name + (f" [{record.variant}]" if record.variant is not None else "")
    where = " › ".join(part for part in (record.path, record.suite) if part)
    message = record.message or ""
    if len(message) > MESSAGE_LIMIT:
        message = message[:MESSAGE_LIMIT] + "…"
    status = record.status.value
    flaky = ' <span class="flaky">flaky</span>' if record.flaky_in_run else ""
    case = record.explicit_id or record.fingerprint[:10]
    return (
        f'<tr><td class="s s-{status}">{status}{flaky}</td>'
        f"<td>{_e(case)}</td><td>{_e(title)}</td><td>{_e(where)}</td>"
        f"<td>{_e(record.configuration or '')}</td><td>{len(record.attempts) or ''}</td>"
        f"<td>{_duration(record.duration_ms)}</td>"
        f'<td class="msg">{_e(message)}</td></tr>'
    )


def render_html(
    upload: RunUpload, title: str = "Test report", generated: datetime | None = None
) -> str:
    """The complete HTML document for ``upload``."""
    when = (generated or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    counts = upload.counts()
    total = len(upload.records)
    executed = total - counts[Status.SKIPPED]
    rate = f"{100 * counts[Status.PASSED] / executed:.1f}%" if executed else "n/a"
    flaky = sum(1 for r in upload.records if r.flaky_in_run)
    tiles = [("tests", total), ("pass rate", rate), ("flaky", flaky)]
    tiles += [(status.value, counts[status]) for status in Status if counts[status]]
    tile_html = "".join(f"<li>{_e(label)}<b>{_e(value)}</b></li>" for label, value in tiles)
    rows = "\n".join(_row(r) for r in sorted(upload.records, key=_sort_key))
    run = upload.name or ""
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_e(title)}</title>\n<style>{_CSS}</style>\n</head>\n<body>\n<main>\n"
        f"<h1>{_e(title)}</h1>\n"
        f'<p class="meta">{_e(run + " · " if run else "")}generated {_e(when)}'
        f" by aurseal {_e(__version__)}</p>\n"
        f'<ul class="tiles">{tile_html}</ul>\n'
        '<div class="table"><table>\n'
        "<thead><tr><th>status</th><th>case</th><th>test</th><th>where</th>"
        "<th>configuration</th><th>attempts</th><th>duration</th><th>message</th></tr></thead>\n"
        f"<tbody>\n{rows}\n</tbody>\n</table></div>\n</main>\n</body>\n</html>\n"
    )


class HtmlReporter:
    """``aurseal.reporters`` entry point: ``index.html`` under the output directory."""

    name = "html"

    def render(self, upload: RunUpload, out: Path) -> Path:
        out.mkdir(parents=True, exist_ok=True)
        target = out / "index.html"
        target.write_text(render_html(upload), encoding="utf-8")
        return target
