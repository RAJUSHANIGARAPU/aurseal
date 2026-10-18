"""Search over cases, failures, documentation sections and defects (SQLite FTS5).

One row per searchable thing in ``search_docs``:

* ``case`` — title, key, description, steps, markers and links of every catalogue case;
* ``failure`` — the message and fingerprint of a failed test, the latest occurrence only
  per (test, fingerprint), so a test failing the same way for months is one row;
* ``doc`` — every ``##`` section of the Markdown pages in ``docs/features/``;
* ``defect`` — every defect id linked to a case, with the titles of its cases.

Queries are FTS5 ``MATCH`` (ranked by ``bm25()``, recent failures boosted) plus filters
parsed out first: ``type:case|failure|doc|defect``, ``outcome:failed``, ``marker:smoke``,
``owner:team-a``, ``since:30d``. User text is always quoted before it reaches ``MATCH``,
so no input can produce an FTS syntax error.

Without FTS5 (a Python whose SQLite lacks it) the migration creates a plain table and
the same API falls back to a LIKE scan, reporting ``engine: "like"``: the degradation is
visible, not silent.

Reindexing is explicit: :func:`reindex` runs at the end of the recorder and of a bundle
import. Failures are incremental (runs newer than the last indexed one); cases, docs and
defects are re-synced each time (they are bounded and change in place).
"""

from __future__ import annotations

import json
import re
import shlex
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from results._util import loads, utc_now
from results.migrations import fts5_available as _fts5_available

DOC_TYPES = ("case", "failure", "doc", "defect")
FILTER_KEYS = ("type", "outcome", "marker", "owner", "since")
INDEX_LIMIT_BYTES = 5 * 1024 * 1024
DEFAULT_DOCS_DIR = Path("docs") / "features"
_SECTION = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def fts5_available(conn: sqlite3.Connection) -> bool:
    """Whether this SQLite build has FTS5."""
    return _fts5_available(conn)


def _is_virtual(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'search_docs'"
    ).fetchone()
    return bool(row and row[0] and "VIRTUAL TABLE" in row[0].upper())


# ---------------------------------------------------------------------- query parsing


@dataclass(frozen=True)
class ParsedQuery:
    terms: tuple[str, ...]  # words and quoted phrases, as typed
    filters: dict[str, str] = field(default_factory=dict)

    def match_expression(self) -> str:
        """The FTS5 MATCH string: every term quoted (``""`` escapes), ANDed."""
        parts = []
        for term in self.terms:
            prefix = term.endswith("*") and len(term) > 1
            text = term[:-1] if prefix else term
            quoted = '"' + text.replace('"', '""') + '"'
            parts.append(quoted + ("*" if prefix else ""))
        return " ".join(parts)


def parse_query(q: str) -> ParsedQuery:
    """Split ``q`` into terms and ``key:value`` filters (unknown keys stay terms)."""
    try:
        tokens = shlex.split(q or "")
    except ValueError:  # an unbalanced quote: keep the words, drop the quotes
        tokens = (q or "").replace('"', " ").replace("'", " ").split()
    terms: list[str] = []
    filters: dict[str, str] = {}
    for token in tokens:
        key, sep, value = token.partition(":")
        if sep and key.lower() in FILTER_KEYS and value:
            filters[key.lower()] = value
        elif token.strip():
            terms.append(token.strip())
    return ParsedQuery(tuple(terms), filters)


def _since_cutoff(value: str) -> str | None:
    match = re.fullmatch(r"(\d+)([dhm])", value.strip())
    if not match:
        return None
    amount, unit = int(match.group(1)), match.group(2)
    delta = {"d": timedelta(days=amount), "h": timedelta(hours=amount)}.get(
        unit, timedelta(minutes=amount)
    )
    return (datetime.now(tz=timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ---------------------------------------------------------------------- search


@dataclass(frozen=True)
class SearchHit:
    doc_type: str
    doc_ref: str
    title: str
    snippet: str
    score: float
    href: str | None = None
    outcome: str | None = None
    recorded_at: str | None = None


@dataclass(frozen=True)
class SearchPage:
    query: str
    engine: str  # "fts5" | "like"
    hits: tuple[SearchHit, ...]
    limit: int
    offset: int
    filters: dict[str, str]

    def to_json(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "engine": self.engine,
            "limit": self.limit,
            "offset": self.offset,
            "filters": self.filters,
            "hits": [asdict(h) for h in self.hits],
        }


def _filter_sql(filters: dict[str, str]) -> tuple[str, list[Any]]:
    clauses, params = [], []
    if "type" in filters:
        clauses.append("doc_type = ?")
        params.append(filters["type"])
    if "outcome" in filters:
        clauses.append("outcome = ?")
        params.append(filters["outcome"])
    if "marker" in filters:
        clauses.append("(' ' || COALESCE(markers, '') || ' ') LIKE ?")
        params.append(f"% {filters['marker']} %")
    if "owner" in filters:
        clauses.append("owner = ?")
        params.append(filters["owner"])
    if "since" in filters:
        cutoff = _since_cutoff(filters["since"])
        if cutoff:
            clauses.append("recorded_at >= ?")
            params.append(cutoff)
    return (" AND ".join(clauses), params)


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _snippet(text: str, terms: tuple[str, ...], width: int = 80) -> str:
    lower = text.lower()
    for term in terms:
        pos = lower.find(term.rstrip("*").lower())
        if pos >= 0:
            start = max(0, pos - width // 2)
            return (
                ("…" if start else "")
                + text[start : start + width]
                + ("…" if start + width < len(text) else "")
            )
    return text[:width] + ("…" if len(text) > width else "")


def search(store: Any, query: str, *, limit: int = 50, offset: int = 0) -> SearchPage:
    """Ranked hits for ``query``; ``engine`` says whether FTS5 or the LIKE fallback ran."""
    conn: sqlite3.Connection = store.conn
    parsed = parse_query(query)
    where, params = _filter_sql(parsed.filters)
    fts = fts5_available(conn) and _is_virtual(conn)
    use_fts = fts and bool(parsed.terms)
    engine = "fts5" if fts else "like"
    cols = "doc_type, doc_ref, title, href, outcome, recorded_at"
    if use_fts:
        sql = (
            f"SELECT {cols}, snippet(search_docs, 1, '[', ']', '…', 12) AS snip,"
            " bm25(search_docs) * CASE WHEN doc_type = 'failure' THEN"
            " 1.0 + 1.0 / (1.0 + MAX(0.0, julianday('now') - julianday(recorded_at)))"
            " ELSE 1.0 END AS score FROM search_docs WHERE search_docs MATCH ?"
            + (f" AND {where}" if where else "")
            + " ORDER BY score, recorded_at DESC LIMIT ? OFFSET ?"
        )
        rows = conn.execute(sql, [parsed.match_expression(), *params, limit, offset]).fetchall()
        hits = tuple(
            SearchHit(r[0], r[1], r[2] or "", r[6] or "", float(r[7]), r[3], r[4], r[5])
            for r in rows
        )
        return SearchPage(query, engine, hits, limit, offset, parsed.filters)
    clauses = [where] if where else []
    like_params: list[Any] = list(params)
    score_parts = []
    for term in parsed.terms:
        pattern = f"%{_like_escape(term.rstrip('*'))}%"
        clauses.append("(title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\')")
        like_params += [pattern, pattern]
        score_parts.append("(title LIKE ? ESCAPE '\\')")
    score = " + ".join(score_parts) or "0"
    sql = (
        f"SELECT {cols}, title, body, -({score}) AS score FROM search_docs"
        + (f" WHERE {' AND '.join(clauses)}" if clauses else "")
        + " ORDER BY score, recorded_at DESC, doc_ref LIMIT ? OFFSET ?"
    )
    title_params = [f"%{_like_escape(t.rstrip('*'))}%" for t in parsed.terms]
    rows = conn.execute(sql, [*title_params, *like_params, limit, offset]).fetchall()
    hits = tuple(
        SearchHit(
            r[0],
            r[1],
            r[2] or "",
            _snippet(r[7] or "", parsed.terms),
            float(r[8]),
            r[3],
            r[4],
            r[5],
        )
        for r in rows
    )
    return SearchPage(query, engine, hits, limit, offset, parsed.filters)


# ---------------------------------------------------------------------- indexing


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "item"


def _insert(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO search_docs (title, body, doc_type, doc_ref, doc_key, project_id, outcome,"
        " markers, owner, recorded_at, href) VALUES (:title, :body, :doc_type, :doc_ref,"
        " :doc_key, :project_id, :outcome, :markers, :owner, :recorded_at, :href)",
        {
            "outcome": None,
            "markers": None,
            "owner": None,
            "recorded_at": None,
            "href": None,
            "doc_key": row.get("doc_ref"),
            "project_id": "default",
            **row,
        },
    )


def _case_rows(store: Any) -> list[dict[str, Any]]:
    out = []
    for c in store.query("cases_all"):
        markers = " ".join(m.get("name", "") for m in loads(c["markers_json"], []) or [])
        steps = " ".join(loads(c["steps_json"], []) or [])
        body = " ".join(
            part
            for part in (c["case_key"], c["description"], steps, markers, c["links_text"])
            if part
        )
        out.append(
            {
                "doc_type": "case",
                "doc_ref": c["case_id"],
                "project_id": c["project_id"],
                "title": c["title"] or c["case_key"],
                "body": body,
                "markers": markers,
                "owner": c["owner"],
                "recorded_at": c["last_seen_at"],
                "outcome": c["status"],
                "href": f"cases/{_safe(c['case_id'])}.html",
            }
        )
    return out


def _doc_rows(docs_dir: Path | None) -> list[dict[str, Any]]:
    if docs_dir is None or not Path(docs_dir).is_dir():
        return []
    out = []
    for page in sorted(Path(docs_dir).glob("*.md")):
        text = page.read_text(encoding="utf-8", errors="replace")
        heads = list(_SECTION.finditer(text))
        title_match = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
        page_title = title_match.group(1) if title_match else page.stem
        if not heads:
            out.append(
                {
                    "doc_type": "doc",
                    "doc_ref": page.name,
                    "title": page_title,
                    "body": text,
                    "doc_key": page.name,
                }
            )
            continue
        for i, head in enumerate(heads):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
            anchor = re.sub(r"[^a-z0-9]+", "-", head.group(1).lower()).strip("-")
            ref = f"{page.name}#{anchor}"
            out.append(
                {
                    "doc_type": "doc",
                    "doc_ref": ref,
                    "doc_key": ref,
                    "title": f"{page_title}: {head.group(1)}",
                    "body": text[head.end() : end].strip(),
                }
            )
    return out


def _defect_rows(store: Any) -> list[dict[str, Any]]:
    return [
        {
            "doc_type": "defect",
            "doc_ref": d["ref"],
            "project_id": d["project_id"],
            "title": d["ref"],
            "body": d["cases"] or "",
        }
        for d in store.query("search_defects")
    ]


def reindex(store: Any, *, full: bool = False, docs_dir: Path | None = DEFAULT_DOCS_DIR) -> int:
    """Bring the index up to date; returns the number of rows written.

    Cases, docs and defects are re-synced. Failures are added for the runs not indexed
    yet (``full`` re-reads every run). Per (test, fingerprint) only the latest occurrence
    is kept, whatever order runs arrive in.
    """
    conn: sqlite3.Connection = store.conn
    written = 0
    with store.transaction():
        if full:
            conn.execute("DELETE FROM search_docs")
            conn.execute("DELETE FROM search_runs")
        else:
            conn.execute("DELETE FROM search_docs WHERE doc_type IN ('case', 'doc', 'defect')")
        for row in [*_case_rows(store), *_doc_rows(docs_dir), *_defect_rows(store)]:
            _insert(conn, row)
            written += 1
        for f in store.query("search_failures_pending", full=1 if full else 0):
            key = f"{f['nodeid']}|{f['failure_fingerprint'] or ''}"
            held = conn.execute(
                "SELECT recorded_at FROM search_docs WHERE doc_type = 'failure' AND doc_key = ?",
                (key,),
            ).fetchone()
            if held is not None and (held[0] or "") > (f["run_started_at"] or ""):
                continue  # a newer occurrence is already indexed
            conn.execute(
                "DELETE FROM search_docs WHERE doc_type = 'failure' AND doc_key = ?", (key,)
            )
            body = " ".join(
                p for p in (f["message"], f["failure_fingerprint"], f["nodeid"], f["title"]) if p
            )
            _insert(
                conn,
                {
                    "doc_type": "failure",
                    "doc_ref": f"{f['run_id']}::{f['nodeid']}",
                    "doc_key": key,
                    "project_id": f["project_id"],
                    "title": f["title"] or f["nodeid"],
                    "body": body,
                    "outcome": f["outcome"],
                    "owner": f["owner"],
                    "markers": " ".join(
                        m.get("name", "") for m in loads(f["markers_json"], []) or []
                    ),
                    "recorded_at": f["run_started_at"],
                    "href": f"runs/{_safe(f['run_id'])}.html",
                },
            )
            written += 1
        now = utc_now()
        conn.execute(
            "INSERT INTO search_runs (project_id, run_id, indexed_at)"
            " SELECT project_id, run_id, ? FROM runs"
            " WHERE run_id NOT IN (SELECT run_id FROM search_runs)",
            (now,),
        )
        latest = conn.execute(
            "SELECT run_id, started_at FROM runs ORDER BY started_at DESC, id DESC LIMIT 1"
        ).fetchone()
        conn.execute(
            "INSERT INTO search_meta (project_id, last_indexed_run_id, last_indexed_at,"
            " indexed_at) VALUES ('default', ?, ?, ?) ON CONFLICT (project_id) DO UPDATE SET"
            " last_indexed_run_id = excluded.last_indexed_run_id,"
            " last_indexed_at = excluded.last_indexed_at, indexed_at = excluded.indexed_at",
            (latest[0] if latest else None, latest[1] if latest else None, now),
        )
    return written


def index_payload(store: Any, *, limit_bytes: int = INDEX_LIMIT_BYTES) -> dict[str, Any]:
    """The rows as a JSON-ready dict for the static dashboard's client-side search.

    Capped at ``limit_bytes``: the oldest failures are dropped first, and the payload
    says how many rows it left out.
    """
    rows = store.conn.execute(
        "SELECT doc_type, doc_ref, title, body, href, recorded_at FROM search_docs"
        " ORDER BY CASE doc_type WHEN 'failure' THEN 1 ELSE 0 END, recorded_at DESC"
    ).fetchall()
    docs, size, dropped = [], 0, 0
    for r in rows:
        doc = {
            "doc_type": r[0],
            "doc_ref": r[1],
            "title": r[2] or "",
            "body": (r[3] or "")[:300],
            "href": r[4],
        }
        cost = len(json.dumps(doc, ensure_ascii=False).encode("utf-8")) + 1
        if size + cost > limit_bytes:
            dropped += 1
            continue
        docs.append(doc)
        size += cost
    return {"generated_at": utc_now(), "docs": docs, "dropped": dropped}


def export_index(store: Any, out: Path) -> Path:
    """Write :func:`index_payload` as ``search-index.json`` (``out`` may be a directory)."""
    target = Path(out)
    if target.suffix != ".json":
        target = target / "search-index.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(index_payload(store), ensure_ascii=False), encoding="utf-8")
    return target


__all__ = [
    "DOC_TYPES",
    "ParsedQuery",
    "SearchHit",
    "SearchPage",
    "export_index",
    "fts5_available",
    "index_payload",
    "parse_query",
    "reindex",
    "search",
]
