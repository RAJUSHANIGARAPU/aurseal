"""``python -m results`` — the store's command line.

Each feature registers its subcommands in :data:`COMMANDS` (a builder that adds parsers
to the top-level subparsers object); ``main`` builds them all.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path

Builder = Callable[[argparse._SubParsersAction], None]
COMMANDS: list[Builder] = []

DEFAULT_OUT = Path("reports") / "results"


def command(builder: Builder) -> Builder:
    COMMANDS.append(builder)
    return builder


def _store(args: argparse.Namespace, *, readonly: bool = False):
    from results.store import open_store

    return open_store(args.db, readonly=readonly)


def _print(obj: object) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def parse_age(text: str) -> str:
    """``180d`` / ``12h`` / ``30m`` -> the ISO-8601 UTC cutoff that long ago."""
    match = re.fullmatch(r"\s*(\d+)\s*([dhm])\s*", text)
    if not match:
        raise argparse.ArgumentTypeError(f"age must look like 180d, 12h or 30m, not {text!r}")
    amount, unit = int(match.group(1)), match.group(2)
    delta = {
        "d": timedelta(days=amount),
        "h": timedelta(hours=amount),
        "m": timedelta(minutes=amount),
    }
    cutoff = datetime.now(tz=timezone.utc) - delta[unit]
    return cutoff.strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ---------------------------------------------------------------------- db


@command
def _db(sub: argparse._SubParsersAction) -> None:
    db = sub.add_parser("db", help="migrations")
    dsub = db.add_subparsers(dest="db_command", required=True)
    dsub.add_parser("migrate", help="apply pending migrations").set_defaults(func=_db_migrate)
    dsub.add_parser("status", help="list migrations and their state").set_defaults(func=_db_status)


def _db_migrate(args: argparse.Namespace) -> int:
    import sqlite3

    from results import migrations
    from results._util import default_db_path

    path = Path(args.db) if args.db else default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        applied = migrations.apply(conn)
    finally:
        conn.close()
    for m in applied:
        print(f"applied {m.domain}/{m.name}")
    print(f"{len(applied)} migration(s) applied to {path}")
    return 0


def _db_status(args: argparse.Namespace) -> int:
    import sqlite3

    from results import migrations
    from results._util import default_db_path

    path = Path(args.db) if args.db else default_db_path()
    conn = sqlite3.connect(str(path), isolation_level=None)
    bad = 0
    try:
        for s in migrations.status(conn):
            state = "applied" if s.applied else "PENDING"
            if s.applied and not s.checksum_ok:
                state, bad = "CHECKSUM MISMATCH", bad + 1
            print(f"{s.domain:12} {s.version:04d} {s.name:40} {state}")
    finally:
        conn.close()
    return 1 if bad else 0


# ---------------------------------------------------------------------- ingest / bundles


@command
def _ingest(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("ingest", help="re-ingest a run's side files (verdicts, data origin)")
    p.add_argument("--run-id", required=True)
    p.add_argument("--verdicts", default=None, help="default: $VERDICTS_FILE")
    p.add_argument("--provenance", default=None, help="default: $DATA_ORIGIN_FILE")
    p.set_defaults(func=_ingest_run)


def _ingest_run(args: argparse.Namespace) -> int:
    with _store(args) as store:
        verdicts = store.ingest_verdicts(args.verdicts, args.run_id)
        usage = store.ingest_provenance(args.provenance, args.run_id)
    print(f"ingested {verdicts} new verdict row(s), {usage} new data-origin row(s)")
    return 0


@command
def _export(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("export", help="export data from the store")
    esub = p.add_subparsers(dest="export_command", required=True)
    b = esub.add_parser("bundle", help="write a run's NDJSON bundle")
    b.add_argument("--run-id", required=True)
    b.add_argument("--out", default=str(DEFAULT_OUT), help="directory or .ndjson[.gz] file")
    b.set_defaults(func=_export_bundle)
    c = esub.add_parser("cases", help="the catalogue as CSV or JSON")
    c.add_argument("--format", choices=("csv", "json"), default="csv")
    c.add_argument("--out", default=None, help="file (default: stdout)")
    c.set_defaults(func=_export_cases)
    r = esub.add_parser("results", help="a run's final results as CSV or JSON")
    r.add_argument("--run-id", required=True)
    r.add_argument("--format", choices=("csv", "json"), default="csv")
    r.add_argument("--out", default=None)
    r.set_defaults(func=_export_results)
    t = esub.add_parser("tms", help="tracker-shaped payloads (formatters only)")
    t.add_argument("tracker", choices=("testrail", "xray"))
    t.add_argument("--run-id", default=None, help="results of this run (else: the cases)")
    t.add_argument("--what", choices=("results", "cases", "junit"), default=None)
    t.add_argument("--out", default=None)
    t.add_argument("--push", action="store_true", help="send it (only --dry-run is built)")
    t.add_argument("--dry-run", action="store_true")
    t.set_defaults(func=_export_tms)


def _export_bundle(args: argparse.Namespace) -> int:
    from results.bundle import write_bundle

    with _store(args) as store:
        if not store.query("run_get", run_id=args.run_id):
            print(f"no run {args.run_id!r} in the store; nothing exported", file=sys.stderr)
            return 2
        path = write_bundle(store, args.run_id, Path(args.out))
    print(f"wrote {path}")
    return 0


def _emit(data: bytes, out: str | None) -> None:
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(data)
        print(f"wrote {out}", file=sys.stderr)
    else:
        sys.stdout.write(data.decode("utf-8"))


def _export_cases(args: argparse.Namespace) -> int:
    from results.io.export import export_cases

    with _store(args) as store:
        _emit(export_cases(store, args.format), args.out)
    return 0


def _export_results(args: argparse.Namespace) -> int:
    from results.io.export import export_results
    from results.store import StoreError

    with _store(args) as store:
        try:
            _emit(export_results(store, args.run_id, args.format), args.out)
        except StoreError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    return 0


def _export_tms(args: argparse.Namespace) -> int:
    from results.catalogue import catalogue_from_store
    from results.io.adapters.base import PushNotSupported, mapping_from_store
    from results.io.adapters.testrail import TestRailAdapter
    from results.io.adapters.xray import XrayAdapter
    from results.io.export import result_rows

    adapter = TestRailAdapter() if args.tracker == "testrail" else XrayAdapter()
    what = args.what or ("results" if args.run_id else "cases")
    with _store(args) as store:
        if what == "cases":
            data = adapter.format_cases(catalogue_from_store(store).cases)
        else:
            run = store.run(args.run_id) if args.run_id else None
            if run is None:
                print(f"no run {args.run_id!r} in the store", file=sys.stderr)
                return 2
            rows = result_rows(store, args.run_id)
            mapping = mapping_from_store(store, adapter.link_kind)
            if what == "junit":
                if not isinstance(adapter, XrayAdapter):
                    print("--what junit is the Xray JUnit flavour", file=sys.stderr)
                    return 2
                data = adapter.format_junit(run, rows, mapping)
            elif isinstance(adapter, XrayAdapter):
                evidence: dict[str, list] = {}
                for ev in store.query("run_evidence", run_id=args.run_id):
                    evidence.setdefault(ev["nodeid"], []).append(ev)
                data = adapter.format_results(run, rows, mapping, evidence)
            else:
                data = adapter.format_results(run, rows, mapping)
    last = adapter.last
    if last is not None and last.unmapped:
        print(f"{len(last.unmapped)} result(s) without a {adapter.link_kind} id:", file=sys.stderr)
        for nodeid in last.unmapped[:20]:
            print(f"  unmapped: {nodeid}", file=sys.stderr)
    if last is not None and last.omitted:
        print(
            f"{len(last.omitted)} result(s) the tracker cannot express (omitted)", file=sys.stderr
        )
    if args.push:
        try:
            report = adapter.push(data, dry_run=args.dry_run)
        except PushNotSupported as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(f"{report.detail}: {report.items} line(s) for {report.tracker}", file=sys.stderr)
    _emit(data, args.out)
    return 0


@command
def _import(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("import", help="import data into the store")
    isub = p.add_subparsers(dest="import_command", required=True)
    b = isub.add_parser("bundle", help="import an NDJSON run bundle")
    b.add_argument("file")
    b.add_argument("--no-validate", action="store_true")
    b.set_defaults(func=_import_bundle)
    j = isub.add_parser("junit", help="import a JUnit XML file (any framework, any language)")
    j.add_argument("file")
    j.add_argument("--framework", default="junit", help="pytest, junit5, jest, go, ...")
    j.add_argument("--run-id", default=None, help="default: derived from the file content")
    j.set_defaults(func=_import_junit)
    m = isub.add_parser("mapping", help="load case -> tracker ids from a two-column CSV")
    m.add_argument("file")
    m.add_argument("--kind", required=True, help="tms:testrail or tms:xray")
    m.set_defaults(func=_import_mapping)


def _import_bundle(args: argparse.Namespace) -> int:
    from results.bundle import BundleError, import_bundle

    with _store(args) as store:
        try:
            report = import_bundle(store, args.file, validate=not args.no_validate)
        except BundleError as exc:
            print(f"bundle rejected: {exc}", file=sys.stderr)
            return 1
    _print(report.to_json())
    return 0


def _import_junit(args: argparse.Namespace) -> int:
    from results.io.junit import JUnitError, import_junit

    with _store(args) as store:
        try:
            report = import_junit(store, args.file, framework=args.framework, run_id=args.run_id)
        except JUnitError as exc:
            print(f"JUnit rejected: {exc}", file=sys.stderr)
            return 1
    _print(report.to_json())
    return 0


def _import_mapping(args: argparse.Namespace) -> int:
    from results.io.mapping import load_mapping

    with _store(args) as store:
        try:
            added = load_mapping(args.file, args.kind, store=store)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    print(f"{added} new {args.kind} link(s)")
    return 0


@command
def _prune(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("prune", help="delete old runs")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--keep-runs", type=int)
    group.add_argument("--older-than", type=parse_age, help="e.g. 180d")
    p.set_defaults(func=_prune_runs)


def _prune_runs(args: argparse.Namespace) -> int:
    with _store(args) as store:
        removed = store.prune(keep_runs=args.keep_runs, older_than=args.older_than)
    print(f"removed {removed} run(s)")
    return 0


# ---------------------------------------------------------------------- catalogue


@command
def _catalogue(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("catalogue", help="test case catalogue generated from code")
    csub = p.add_subparsers(dest="catalogue_command", required=True)
    b = csub.add_parser("build", help="collect (never run) and write catalogue.json/.md")
    b.add_argument("--out", default="reports/catalogue", help="output directory")
    b.add_argument("--rootdir", default=".", help="project root to collect from")
    b.add_argument("--diff-against", default=None, help="an earlier catalogue.json")
    b.add_argument("--diff-format", choices=("md", "json"), default="md")
    b.add_argument("--no-store", action="store_true", help="do not update the results store")
    b.add_argument("pytest_args", nargs=argparse.REMAINDER, help="selection (makes it partial)")
    b.set_defaults(func=_catalogue_build)


def _catalogue_build(args: argparse.Namespace) -> int:
    from results.catalogue import (
        build_catalogue,
        diff_catalogues,
        load_catalogue,
        store_catalogue,
        write_catalogue,
    )

    extra = [a for a in args.pytest_args if a != "--"]
    cat = build_catalogue(Path(args.rootdir), extra)
    json_path, md_path = write_catalogue(cat, Path(args.out))
    print(f"{len(cat.cases)} case(s) -> {json_path}, {md_path}")
    for err in cat.errors:
        print(f"collection problem: {err}", file=sys.stderr)
    if not cat.complete:
        print("partial collection: no case is marked removed", file=sys.stderr)
    if not args.no_store:
        with _store(args) as store:
            removed = store_catalogue(store, cat)
        print(f"store updated; {removed} case(s) marked removed")
    if args.diff_against:
        diff = diff_catalogues(load_catalogue(Path(args.diff_against)), cat)
        if args.diff_format == "json":
            _print(diff.to_json())
        else:
            print(diff.to_markdown())
    return 1 if cat.errors else 0


@command
def _case(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("case", help="one test case")
    csub = p.add_subparsers(dest="case_command", required=True)
    r = csub.add_parser("revisions", help="a case's revisions and the runs each covered")
    r.add_argument("case_id")
    r.add_argument("--project", default="default")
    r.set_defaults(func=_case_revisions)


def _case_revisions(args: argparse.Namespace) -> int:
    from results.revision import revision_ranges

    with _store(args) as store:
        ranges = revision_ranges(store, args.case_id, project_id=args.project)
    if not ranges:
        print(f"no revisions stored for case {args.case_id!r}", file=sys.stderr)
        return 2
    for r in ranges:
        print(
            f"{r['revision_id'][:12]}  first seen {r['first_seen_run_id'] or '-'}  "
            f"runs {r['run_count']} ({r['first_run_id'] or '-'} .. {r['last_run_id'] or '-'})  "
            f"git {(r['git_sha'] or '-')[:12]}"
        )
    return 0


# ---------------------------------------------------------------------- trends


@command
def _trends(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("trends", help="history and trends")
    tsub = p.add_subparsers(dest="trends_command", required=True)
    c = tsub.add_parser("case", help="one case's trend")
    c.add_argument("case_id")
    c.add_argument("--window", type=int, default=30)
    c.add_argument("--include-unsound", action="store_true")
    c.set_defaults(func=_trends_case)
    s = tsub.add_parser("suite", help="per-run suite trend")
    s.add_argument("--lane", default=None)
    s.add_argument("--branch", default=None)
    s.add_argument("--window", type=int, default=30)
    s.set_defaults(func=_trends_suite)
    for name, func in (("unstable", _trends_unstable), ("slowest", _trends_slowest)):
        t = tsub.add_parser(name, help=f"top list: {name} cases")
        t.add_argument("--window", type=int, default=30)
        t.add_argument("--lane", default=None)
        t.set_defaults(func=func)


def _trends_case(args: argparse.Namespace) -> int:
    from results.trends import case_trend

    with _store(args) as store:
        trend = case_trend(
            store, args.case_id, window=args.window, include_unsound=args.include_unsound
        )
    _print(trend.to_json())
    return 0


def _trends_suite(args: argparse.Namespace) -> int:
    from results.trends import suite_trend

    with _store(args) as store:
        points = suite_trend(store, lane=args.lane, branch=args.branch, window=args.window)
    for p in points:
        flag = "  UNSOUND" if p.unsound else ""
        print(f"{p.started_at or '-':24} {p.run_id:32} {p.rate_text}{flag}")
    if not points:
        print("no runs stored")
    return 0


def _trends_unstable(args: argparse.Namespace) -> int:
    from results.trends import unstable

    with _store(args) as store:
        _print(unstable(store, window=args.window, lane=args.lane))
    return 0


def _trends_slowest(args: argparse.Namespace) -> int:
    from results.trends import slowest

    with _store(args) as store:
        _print(slowest(store, window=args.window, lane=args.lane))
    return 0


@command
def _compare(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("compare", help="compare a run with its baseline")
    p.add_argument("run_id")
    p.add_argument("--baseline", default=None)
    p.add_argument("--format", choices=("md", "json"), default="md")
    p.set_defaults(func=_compare_runs)


def _compare_runs(args: argparse.Namespace) -> int:
    from results.store import StoreError
    from results.trends import compare_runs

    with _store(args) as store:
        try:
            cmp = compare_runs(store, args.run_id, args.baseline)
        except StoreError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    if args.format == "json":
        _print(cmp.to_json())
    else:
        print(cmp.to_markdown())
    return 0


# ---------------------------------------------------------------------- scope


@command
def _scope(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("scope", help="tests out of scope in every recent run (removal candidates)")
    p.add_argument("--runs", type=int, default=10, help="how many recent runs must agree")
    p.set_defaults(func=_scope_report)


def _scope_report(args: argparse.Namespace) -> int:
    from results.scope import removal_candidates, report

    with _store(args, readonly=True) as store:
        print(report(removal_candidates(store, args.runs), args.runs))
    return 0


# ---------------------------------------------------------------------- history branch


@command
def _history(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("history", help="run bundles kept on the results-history branch")
    hsub = p.add_subparsers(dest="history_command", required=True)
    a = hsub.add_parser("add", help="add a run (from the store, or a bundle file)")
    a.add_argument("--dir", default=".results-history")
    group = a.add_mutually_exclusive_group(required=True)
    group.add_argument("--run-id")
    group.add_argument("--bundle", nargs="+")
    a.set_defaults(func=_history_add)
    r = hsub.add_parser("rebuild", help="import every history bundle into the store")
    r.add_argument("--dir", default=".results-history")
    r.set_defaults(func=_history_rebuild)
    pr = hsub.add_parser("prune", help="keep only the newest bundles")
    pr.add_argument("--dir", default=".results-history")
    pr.add_argument("--keep-runs", type=int, required=True)
    pr.set_defaults(func=_history_prune)


def _history_add(args: argparse.Namespace) -> int:
    from results.history import add_bundle_file, add_run

    if args.bundle:
        for bundle in args.bundle:
            print(f"added {add_bundle_file(Path(bundle), Path(args.dir))}")
        return 0
    with _store(args) as store:
        print(f"added {add_run(store, args.run_id, Path(args.dir))}")
    return 0


def _history_rebuild(args: argparse.Namespace) -> int:
    from results.history import rebuild

    with _store(args) as store:
        report = rebuild(store, Path(args.dir))
    print(f"imported {len(report.imported)} bundle(s), {len(report.runs)} run(s)")
    for failure in report.failed:
        print(f"FAILED {failure['bundle']}: {failure['error']}", file=sys.stderr)
    return 1 if report.failed else 0


def _history_prune(args: argparse.Namespace) -> int:
    from results.history import prune_history

    removed = prune_history(Path(args.dir), keep_runs=args.keep_runs)
    print(f"removed {len(removed)} bundle(s)")
    return 0


# ---------------------------------------------------------------------- dashboard


@command
def _dashboard(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("dashboard", help="render the static HTML dashboard")
    p.add_argument("--out", default="reports/dashboard")
    p.add_argument("--window", type=int, default=30)
    p.add_argument("--base-artifact-url", default=None, help="prefix for evidence links")
    p.set_defaults(func=_dashboard_render)


def _dashboard_render(args: argparse.Namespace) -> int:
    from results.dashboard import render

    with _store(args) as store:
        written = render(
            store, Path(args.out), window=args.window, base_artifact_url=args.base_artifact_url
        )
    print(f"wrote {len(written)} page(s) to {args.out}")
    return 0


# ---------------------------------------------------------------------- search


@command
def _search(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("search", help="full-text search over cases, failures, docs, defects")
    p.add_argument("query", nargs="?", default="")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.add_argument("--reindex", action="store_true", help="update the index first")
    p.add_argument("--full", action="store_true", help="with --reindex: rebuild everything")
    p.add_argument("--docs-dir", default="docs/features")
    p.add_argument("--export", default=None, help="write search-index.json to this path")
    p.set_defaults(func=_search_run)


def _search_run(args: argparse.Namespace) -> int:
    from results.search import export_index, reindex, search

    with _store(args) as store:
        if args.reindex:
            rows = reindex(store, full=args.full, docs_dir=Path(args.docs_dir))
            print(f"indexed {rows} row(s)")
        if args.export:
            print(f"wrote {export_index(store, Path(args.export))}")
        if not args.query:
            return 0
        page = search(store, args.query, limit=args.limit, offset=args.offset)
    if args.format == "json":
        _print(page.to_json())
        return 0
    print(f"engine: {page.engine}; {len(page.hits)} hit(s)")
    for hit in page.hits:
        print(f"[{hit.doc_type}] {hit.title}  ({hit.doc_ref})")
        if hit.snippet:
            print(f"    {hit.snippet}")
    return 0


# ---------------------------------------------------------------------- living docs


@command
def _livedocs(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("livedocs", help="render living documentation, or lint it (--check)")
    p.add_argument("--docs-dir", default="docs/features")
    p.add_argument("--out", default="reports/living-docs")
    p.add_argument("--check", action="store_true", help="lint docs <-> tests links; no DB")
    p.add_argument("--catalogue", default=None, help="catalogue.json to lint against")
    p.add_argument("--rootdir", default=".", help="project to collect when no --catalogue")
    p.set_defaults(func=_livedocs_run)


def _livedocs_run(args: argparse.Namespace) -> int:
    from results import livedocs

    if args.check:
        from results.catalogue import build_catalogue, load_catalogue

        cat = (
            load_catalogue(Path(args.catalogue))
            if args.catalogue
            else build_catalogue(Path(args.rootdir))
        )
        issues = livedocs.check(Path(args.docs_dir), cat)
        for issue in issues:
            print(issue)
        print(f"living docs check: {len(issues)} issue(s)")
        return 1 if issues else 0
    with _store(args) as store:
        written = livedocs.build(store, Path(args.docs_dir), Path(args.out))
    print(f"wrote {len(written)} file(s) to {args.out}")
    return 0


# ---------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    # Feature modules register their commands on import.
    for module in _FEATURE_MODULES:
        __import__(module)
    parser = argparse.ArgumentParser(prog="python -m results", description=__doc__)
    parser.add_argument("--db", default=None, help="store path (default: $RESULTS_DB)")
    sub = parser.add_subparsers(dest="command", required=True)
    for builder in COMMANDS:
        builder(sub)
    return parser


_FEATURE_MODULES: list[str] = []


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


__all__ = ["COMMANDS", "build_parser", "command", "main", "parse_age"]
