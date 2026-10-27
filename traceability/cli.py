"""``python -m traceability`` — one entry point; each feature registers its subcommands.

Exit codes: 0 ok, 1 a check found a problem, 2 usage or input error. ``release decide``
adds 3 for NO_VERDICT.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

Builder = Callable[[argparse._SubParsersAction], None]
COMMANDS: list[Builder] = []


def command(builder: Builder) -> Builder:
    COMMANDS.append(builder)
    return builder


def print_json(obj: object) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def open_db(path: str | None):
    from results.store import open_store

    return open_store(path)


# ---------------------------------------------------------------------- links (G-003)


@command
def _links(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("links", help="requirement links: check ids, show or store them")
    p.add_argument("--check", action="store_true", help="collect-only check of every marker id")
    p.add_argument("--strict", action="store_true", help="exit 1 on any bad id (or set strict env)")
    p.add_argument("--rootdir", default=".", help="suite root to collect")
    p.add_argument("--reports", help="show the case-links file(s) of a finished run")
    p.add_argument("--db", help="with --reports: store the links and the registry here")
    p.add_argument("pytest_args", nargs="*", help="extra pytest selection arguments")
    p.set_defaults(func=_links_run)


def _links_run(args: argparse.Namespace) -> int:
    from traceability._util import env_flag
    from traceability.requirements import STRICT_ENV, Registry

    if args.check:
        from traceability.collect import collect

        result = collect(Path(args.rootdir), args.pytest_args)
        strict = args.strict or env_flag(STRICT_ENV)
        bad = [b for item in result.items for b in item.get("invalid") or ()]
        linked = sum(1 for item in result.items if item.get("requirements"))
        for b in bad:
            print(f"{b['nodeid']}: {b['id']} ({b['problem']})")
        for err in result.errors:
            print(f"collection: {err}", file=sys.stderr)
        print(f"{len(result.items)} tests, {linked} linked, {len(bad)} bad requirement id(s)")
        if result.errors:
            return 2
        return 1 if bad and strict else 0
    if args.reports:
        from traceability.links import load_all

        doc = load_all(Path(args.reports))
        if args.db:
            from traceability.store import save_links, save_registry

            with open_db(args.db) as store:
                save_registry(store, Registry.load(rootdir=Path(args.rootdir)))
                added, unknown = save_links(store, doc.links)
            print(f"stored {added} new link(s); {len(unknown)} for cases not in the catalogue")
            return 0
        print_json(doc.to_json())
        return 0
    print("links: pass --check or --reports", file=sys.stderr)
    return 2


# ---------------------------------------------------------------------- matrix (G-004)


def add_source_args(p: argparse.ArgumentParser, *, release_scope: bool = True) -> None:
    """Where results come from: a reports directory, or the store plus an explicit scope."""
    p.add_argument("--reports", help="reports directory of a run (artefacts, no database)")
    p.add_argument("--db", help="results store instead of artefacts")
    p.add_argument("--runs", nargs="+", default=[], help="run ids in scope")
    p.add_argument("--branch", help="store scope: runs on this branch")
    p.add_argument("--last", type=int, help="store scope: the newest N runs (with --branch)")
    if release_scope:
        p.add_argument("--release", help="store scope: runs tagged with this release label")
    p.add_argument("--registry", help="requirements file (default: the repository registry)")


def source_from_args(args: argparse.Namespace, requirements: Sequence[str] = ()):
    """``(ResultSource, Scope, store-or-None)`` for the shared source options."""
    from traceability.evidence import ArtifactResultSource, Scope, StoreResultSource

    scope = Scope(
        run_ids=tuple(args.runs),
        branch=args.branch,
        last=args.last,
        release=getattr(args, "release", None),
        requirements=tuple(requirements),
    )
    if args.db:
        store = open_db(args.db)
        return StoreResultSource(store, scope), scope, store
    if not args.reports:
        raise ValueError("pass --reports DIR (artefacts) or --db PATH with a scope")
    return ArtifactResultSource(Path(args.reports), tuple(args.runs)), scope, None


@command
def _matrix(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("matrix", help="requirements traceability matrix (report only)")
    add_source_args(p)
    p.add_argument(
        "--out",
        default="reports/traceability",
        help="writes matrix.json, .md, .csv and headline.txt",
    )
    p.add_argument("--requirements", nargs="+", default=[], help="only these requirement ids")
    p.add_argument("--save", action="store_true", help="with --db: store a snapshot")
    p.set_defaults(func=_matrix_run)


def _matrix_run(args: argparse.Namespace) -> int:
    from traceability._util import write_json, write_text
    from traceability.matrix import build_matrix, matrix_json, render_csv, render_markdown
    from traceability.requirements import Registry

    registry = Registry.load(args.registry)
    source, scope, store = source_from_args(args, args.requirements)
    try:
        matrix = build_matrix(registry, None, source, scope)
        doc = matrix_json(matrix)
        if store is not None and args.save:
            from traceability.store import save_matrix

            doc["snapshot_id"] = save_matrix(store, doc)
    finally:
        if store is not None:
            store.close()
    out = Path(args.out)
    write_json(out / "matrix.json", doc)
    write_text(out / "matrix.md", render_markdown(matrix))
    write_text(out / "matrix.csv", render_csv(matrix))
    write_text(out / "headline.txt", matrix.headline_line + "\n")
    print(matrix.headline)
    print(matrix.headline_line)
    return 0


# ---------------------------------------------------------------------- plans (G-005)


@command
def _plan(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("plan", help="test plans: lint, resolve, evaluate")
    psub = p.add_subparsers(dest="plan_command", required=True)
    lint = psub.add_parser("lint", help="every plan validates and every selector matches")
    lint.add_argument("directory", nargs="?", default="plans")
    lint.add_argument("--rootdir", default=".")
    lint.set_defaults(func=_plan_lint)
    res = psub.add_parser("resolve", help="the exact tests a plan selects")
    res.add_argument("plan")
    res.add_argument("--rootdir", default=".")
    res.set_defaults(func=_plan_resolve)
    ev = psub.add_parser("evaluate", help="score the exit criteria against runs")
    ev.add_argument("plan")
    add_source_args(ev)
    ev.add_argument("--rootdir", default=".")
    ev.add_argument("--no-resolve", action="store_true", help="judge every test in the runs")
    ev.add_argument("--out", default="reports/traceability")
    ev.add_argument("--save", action="store_true", help="with --db: store the evaluation")
    ev.set_defaults(func=_plan_evaluate)


def _plan_lint(args: argparse.Namespace) -> int:
    from traceability.plans import lint_plans

    problems = lint_plans(Path(args.directory), Path(args.rootdir))
    for line in problems:
        print(line)
    print(f"plan lint: {len(problems)} problem(s)")
    return 1 if problems else 0


def _plan_resolve(args: argparse.Namespace) -> int:
    from traceability.plans import load_plan, resolve_plan

    sel = resolve_plan(load_plan(args.plan), Path(args.rootdir))
    print_json(sel.to_json())
    return 0 if sel.ok else 1


def _plan_evaluate(args: argparse.Namespace) -> int:
    from traceability._util import write_json, write_text
    from traceability.plans import evaluate_plan, load_plan, resolve_plan
    from traceability.requirements import Registry

    plan = load_plan(args.plan)
    registry = Registry.load(args.registry, rootdir=Path(args.rootdir))
    selection = None if args.no_resolve else resolve_plan(plan, Path(args.rootdir), registry)
    source, _scope, store = source_from_args(args)
    try:
        evaluation = evaluate_plan(
            plan,
            source,
            args.runs,
            registry=registry,
            selection=selection,
            open_defects=open_defects_provider(),
        )
        if store is not None and args.save:
            from traceability.store import save_plan, save_plan_evaluation

            save_plan(store, plan)
            save_plan_evaluation(store, evaluation)
    finally:
        if store is not None:
            store.close()
    out = Path(args.out)
    write_json(out / f"plan-{plan.id}.json", evaluation.to_json())
    write_text(out / f"plan-{plan.id}.md", evaluation.to_markdown())
    print(f"plan {plan.id}: {evaluation.result}")
    return {"met": 0, "unmet": 1}.get(evaluation.result, 3)


def open_defects_provider():
    """Open defects from the last ``defects sync`` snapshot (a missing one is no evidence)."""
    from traceability.defects import open_defects_from_snapshot

    return open_defects_from_snapshot()


# ---------------------------------------------------------------------- gaps (G-006)


@command
def _gaps(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("gaps", help="coverage gaps against OpenAPI, specs, Postman, requirements")
    p.add_argument(
        "--openapi",
        nargs="*",
        action="extend",
        default=[],
        help="OpenAPI JSON file(s) or http(s) URL(s) (repeatable)",
    )
    p.add_argument("--specs", help="directory of spec markdown files")
    p.add_argument(
        "--postman",
        nargs="*",
        action="extend",
        default=[],
        help="Postman collection file(s) or URL(s) (repeatable)",
    )
    p.add_argument("--requirements", action="store_true", help="include the registry")
    p.add_argument("--hits", help="endpoint-hits.jsonl from a run (runtime mode)")
    p.add_argument("--reports", help="a run's reports dir: outcomes for covered_not_passing")
    p.add_argument("--baseline", help="ratchet against this accepted-gaps file")
    p.add_argument("--write-baseline", action="store_true", help="rewrite the baseline")
    p.add_argument("--report-only", action="store_true", help="never fail on new gaps")
    p.add_argument("--rootdir", default=".")
    p.add_argument("--out", default="reports/traceability")
    p.add_argument("--db", help="also store the report (with --run-id / --branch)")
    p.add_argument("--run-id")
    p.add_argument("--branch")
    p.set_defaults(func=_gaps_run)


def _gaps_run(args: argparse.Namespace) -> int:
    from traceability._util import write_json, write_text
    from traceability.collect import collect
    from traceability.endpoint_hits import read_hits
    from traceability.gaps import (
        OpenApiSource,
        PostmanSource,
        RequirementSource,
        SpecSource,
        detect_gaps,
        location,
        ratchet,
        report_json,
        write_baseline,
    )
    from traceability.requirements import Registry

    root = Path(args.rootdir)
    sources = [OpenApiSource(location(p)) for p in args.openapi]
    if args.specs:
        sources.append(SpecSource(Path(args.specs)))
    sources += [PostmanSource(location(p)) for p in args.postman]
    if args.requirements:
        sources.append(RequirementSource(Registry.load(rootdir=root)))
    if not sources:
        raise ValueError("name at least one source (--openapi, --specs, --postman, --requirements)")
    collection = collect(root)
    if collection.errors:
        for err in collection.errors:
            print(f"collection: {err}", file=sys.stderr)
        return 2
    hits = read_hits(Path(args.hits)) if args.hits else []
    outcomes = None
    if args.reports:
        from traceability.evidence import ArtifactResultSource, latest_per_test

        src = ArtifactResultSource(Path(args.reports))
        outcomes = {k: v.outcome for k, v in latest_per_test(src.results(), src.runs()).items()}
    report = detect_gaps(sources, collection.items, hits, outcomes)
    result = None
    if args.write_baseline and args.baseline:
        write_baseline(report, Path(args.baseline))
        print(f"baseline written: {len(report.gaps)} accepted gap(s)")
    elif args.baseline:
        result = ratchet(report, Path(args.baseline))
    doc = report_json(report)
    out = Path(args.out)
    write_json(out / "gaps.json", doc)
    write_text(out / "gaps.md", report.to_markdown())
    if args.db:
        import hashlib

        from traceability.store import save_gap_report

        digest = None
        if args.baseline and Path(args.baseline).is_file():
            digest = hashlib.sha256(Path(args.baseline).read_bytes()).hexdigest()
        with open_db(args.db) as store:
            save_gap_report(
                store, doc, run_id=args.run_id, branch=args.branch, baseline_hash=digest
            )
    counts = doc["counts"]
    print(" · ".join(f"{k} {v}" for k, v in counts.items()))
    if result is not None:
        for gap in result.new_gaps:
            print(f"NEW GAP: {gap}")
        if result.closed_gaps:
            print(f"{len(result.closed_gaps)} gap(s) closed: remove them from {args.baseline}")
        if not result.ok and not args.report_only:
            return 1
    return 0


# ---------------------------------------------------------------------- workflow gaps


@command
def _workflow_gaps(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("workflow-gaps", help="workflow (state, action) pairs no test names")
    p.add_argument("--config-dir", help="workflow definitions (default $EXT_CONFIG_PATH)")
    p.add_argument(
        "--tests",
        nargs="+",
        help="test roots scanned for string literals (default tests ui/tests api/tests)",
    )
    p.add_argument("--baseline", help="ratchet against this accepted-gaps file")
    p.add_argument("--write-baseline", action="store_true", help="rewrite the baseline")
    p.add_argument("--report-only", action="store_true", help="never fail")
    p.add_argument("--out", default="reports/traceability")
    p.set_defaults(func=_workflow_gaps_run)


def _workflow_gaps_run(args: argparse.Namespace) -> int:
    """0 clean; 1 a new gap or a stale baseline entry; 3 NOT-MEASURED (unbound config)."""
    from traceability._util import write_json, write_text
    from traceability.workflow_gaps import (
        DEFAULT_TEST_ROOTS,
        measure,
        report_json,
        write_baseline,
    )

    baseline = Path(args.baseline) if args.baseline else None
    roots = args.tests or DEFAULT_TEST_ROOTS
    report = measure(args.config_dir, roots, None if args.write_baseline else baseline)
    if args.write_baseline and baseline is not None and report.measured:
        write_baseline(report, baseline)
        print(f"baseline written: {len(report.gaps)} accepted gap(s)")
    out = Path(args.out)
    write_json(out / "workflow-gaps.json", report_json(report))
    write_text(out / "workflow-gaps.md", report.to_markdown())
    print(report.header())
    if not report.measured:
        print(f"NOT MEASURED: {report.not_measured}")
        return 0 if args.report_only else 3
    for gap in report.new:
        print(f"NEW GAP: {gap}")
    for entry in report.stale:
        print(f"STALE: {entry} is no longer a gap; remove it from {args.baseline}")
    return 0 if report.ok or args.report_only else 1


# ---------------------------------------------------------------------- review (G-007)


@command
def _review(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("review", help="generated tests: draft lint and the review queue")
    rsub = p.add_subparsers(dest="review_command", required=True)
    lint = rsub.add_parser("lint", help="every generated test is a draft or was approved")
    lint.add_argument("paths", nargs="*", help="generator output paths (default: configured)")
    lint.set_defaults(func=_review_lint)
    queue = rsub.add_parser("queue", help="tests awaiting review")
    queue.add_argument("--rootdir", default=".")
    queue.set_defaults(func=_review_queue)


def _review_lint(args: argparse.Namespace) -> int:
    from traceability.review import generated_paths, lint_generated

    paths = [Path(p) for p in args.paths] or generated_paths()
    findings = lint_generated(paths)
    for finding in findings:
        print(finding)
    print(f"review lint: {len(findings)} problem(s) in {', '.join(map(str, paths))}")
    return 1 if findings else 0


def _review_queue(args: argparse.Namespace) -> int:
    from traceability.collect import collect

    result = collect(Path(args.rootdir))
    drafts = [i for i in result.items if i.get("review_state") == "draft"]
    for item in drafts:
        print(
            f"{item['nodeid']}  generator={item.get('generator', '?')}  "
            f"source={item.get('source', '?')}"
        )
    print(f"{len(drafts)} test(s) awaiting review")
    return 0


# ---------------------------------------------------------------------- release (G-022)


@command
def _release(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("release", help="release quality verdict")
    rsub = p.add_subparsers(dest="release_command", required=True)
    d = rsub.add_parser("decide", help="GO / GO_WITH_WAIVERS / NO_GO / NO_VERDICT")
    d.add_argument("--release", dest="release_id", required=True)
    d.add_argument("--candidate", required=True)
    d.add_argument("--version")
    d.add_argument("--commit")
    add_source_args(d, release_scope=False)
    d.add_argument("--plan", help="plan file whose exit criteria fold into the verdict")
    d.add_argument("--policy", default="release-policy.yaml")
    d.add_argument("--waivers", default="release-waivers.yaml")
    d.add_argument("--rootdir", default=".")
    d.add_argument("--out", default="reports/release")
    d.add_argument("--save", action="store_true", help="with --db: store candidate + verdict")
    d.add_argument(
        "--release-line", help="detectors whose fix ships in another line are out of scope"
    )
    d.add_argument(
        "--known-defects",
        help="known-defects manifest file or directory: the fix_line of each defect",
    )
    d.set_defaults(func=_release_decide)


def _release_decide(args: argparse.Namespace) -> int:
    from traceability._util import write_json, write_text
    from traceability.release import (
        EXIT_CODES,
        Candidate,
        decide_release,
        decision_json,
        load_policy,
        load_waivers,
    )
    from traceability.requirements import Registry

    # With --db and no explicit runs, the candidate's runs are those tagged with the release.
    args.release = args.release_id if args.db and not (args.runs or args.branch) else None
    plan = selection = None
    if args.plan:
        from traceability.plans import load_plan, resolve_plan

        plan = load_plan(args.plan)
    registry = Registry.load(args.registry, rootdir=Path(args.rootdir))
    if plan is not None:
        selection = resolve_plan(plan, Path(args.rootdir), registry)
    candidate = Candidate(
        args.release_id, args.candidate, args.version, args.commit, plan.id if plan else None
    )
    fix_lines = None
    if args.known_defects:
        from verification.known_defects_manifest import fix_lines as manifest_fix_lines
        from verification.known_defects_manifest import load_manifest

        fix_lines = manifest_fix_lines(load_manifest(args.known_defects))
    source, _scope, store = source_from_args(args)
    try:
        decision = decide_release(
            candidate,
            source,
            load_policy(args.policy),
            registry=registry,
            waivers=load_waivers(args.waivers),
            plan=plan,
            selection=selection,
            open_defects=open_defects_provider(),
            release_line=args.release_line,
            fix_lines=fix_lines,
        )
        doc = decision_json(decision)
        if store is not None and args.save:
            from traceability.store import save_candidate, save_release_verdict

            save_candidate(
                store,
                candidate,
                decision.evidence_runs,
                plan_revision=plan.revision if plan else None,
            )
            save_release_verdict(store, {**doc, "matrix": decision.matrix})
    finally:
        if store is not None:
            store.close()
    out = Path(args.out)
    stem = f"{args.release_id}-{args.candidate}"
    write_json(out / f"{stem}.json", doc)
    write_text(out / f"{stem}.md", decision.to_markdown())
    print(decision.to_markdown())
    return EXIT_CODES[decision.verdict]


# ---------------------------------------------------------------------- defects (G-025)


@command
def _defects(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("defects", help="defect tracker: sync states, draft or file issues")
    dsub = p.add_subparsers(dest="defects_command", required=True)
    sync = dsub.add_parser("sync", help="refresh the defect-status snapshot from the trackers")
    sync.add_argument("--ids", nargs="*", default=[], help="extra defect ids to look up")
    sync.add_argument("--rootdir", default=".", help="collect known_defect ids from this suite")
    sync.add_argument("--no-collect", action="store_true", help="do not collect marker ids")
    sync.add_argument("--trackers", help="tracker config (default integrations/trackers.yaml)")
    sync.add_argument("--snapshot", help="snapshot path (default $DEFECT_STATUS_FILE or reports/)")
    sync.add_argument("--db", help="also store the states")
    sync.set_defaults(func=_defects_sync)
    draft = dsub.add_parser("draft", help="draft issues for a run's failures (files nothing)")
    draft.add_argument("--from-run", required=True, help="the run's reports directory")
    draft.add_argument("--out", default="reports/traceability/defect-drafts")
    draft.add_argument("--submit", action="store_true", help="file new drafts in the tracker")
    draft.add_argument("--tracker", default="github", help="tracker name to file in")
    draft.add_argument("--trackers", help="tracker config")
    draft.add_argument("--flaky", help="JSON list of node ids never to file")
    draft.set_defaults(func=_defects_draft)


def _defects_sync(args: argparse.Namespace) -> int:
    from integrations.trackers.base import TrackerConfig
    from traceability.defects import StatusCache, referenced_ids

    config = TrackerConfig.load(args.trackers, rootdir=Path(args.rootdir))
    ids = set(args.ids) | set(referenced_ids())
    if not args.no_collect:
        from traceability.collect import collect

        result = collect(Path(args.rootdir), ["-p", "traceability._defect_ids"])
        ids |= {d for item in result.items for d in item.get("defects") or ()}
    cache = StatusCache(config, Path(args.snapshot) if args.snapshot else None)
    snap = cache.sync(ids)
    if args.db:
        from traceability.store import save_defects

        with open_db(args.db) as store:
            save_defects(store, snap)
    closed = sorted(snap.closed_ids())
    print(
        f"{len(snap.defects)} defect(s), {len(snap.open_ids)} open, closed: "
        f"{', '.join(closed) or 'none'} -> {cache.snapshot}"
    )
    for error in snap.errors:
        print(f"warning: {error}", file=sys.stderr)
    print(f"export DEFECT_STATUS_FILE={cache.snapshot}")
    return 0


def _defects_draft(args: argparse.Namespace) -> int:
    import json as _json

    from integrations.trackers.base import TrackerConfig, build
    from traceability.defects import (
        autofile_allowed,
        draft_from_failure,
        failures_from_reports,
        file_drafts,
        write_drafts,
    )

    drafts = [
        draft_from_failure(None, row, nodeid=nodeid, run_id=run_id, message=message)
        for run_id, nodeid, message, row in failures_from_reports(Path(args.from_run))
    ]
    submit = args.submit or autofile_allowed()
    tracker = None
    if submit:
        config = TrackerConfig.load(args.trackers)
        if args.tracker not in config.trackers:
            raise ValueError(f"no tracker named {args.tracker!r} in {config.path}")
        tracker = build(args.tracker, config.trackers[args.tracker])
    flaky = _json.loads(Path(args.flaky).read_text()) if args.flaky else []
    outcomes = file_drafts(drafts, tracker, submit=submit, exclude=flaky)
    write_drafts(outcomes, Path(args.out))
    for o in outcomes:
        print(f"{o.action:8} {o.ref or '-':10} {o.draft.nodeid}")
    print(f"{len(outcomes)} draft(s) in {args.out}; submit={'yes' if submit else 'no'}")
    return 0


# ---------------------------------------------------------------------- requirements (G-026)


@command
def _requirements(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("requirements", help="requirements sync and listing")
    rsub = p.add_subparsers(dest="requirements_command", required=True)
    s = rsub.add_parser("sync", help="merge sources into the snapshot (dry run by default)")
    s.add_argument(
        "--source",
        action="append",
        choices=["local", "github"],
        help="sources in precedence order (default: local)",
    )
    s.add_argument("--local", default="requirements/requirements.yaml")
    s.add_argument("--repo", help="GitHub owner/name (default $GITHUB_REPOSITORY)")
    s.add_argument("--label", default="requirement")
    s.add_argument("--number-prefix", help="id prefix for issues without an id in the title")
    s.add_argument("--snapshot", default="requirements/snapshot.json")
    s.add_argument("--write", action="store_true", help="write the snapshot")
    s.add_argument("--pr-body", help="write the diff as Markdown here (for a bot pull request)")
    s.add_argument("--links", help="reports dir whose case-links find orphaned links")
    s.add_argument("--rootdir", default=".", help="else collect the suite for links")
    s.add_argument("--no-collect", action="store_true")
    s.add_argument("--db", help="also store the sync and the requirements")
    s.set_defaults(func=_requirements_sync)
    show = rsub.add_parser("show", help="the registry the marker check uses")
    show.add_argument("--rootdir", default=".")
    show.set_defaults(func=_requirements_show)


def _requirements_sync(args: argparse.Namespace) -> int:
    import os

    from integrations.requirements.base import GitHubIssuesSource, LocalYamlSource
    from traceability.links import CaseLink, load_all
    from traceability.requirements import Registry
    from traceability.requirements_sync import sync

    sources = []
    for name in args.source or ["local"]:
        if name == "local":
            sources.append(LocalYamlSource(Path(args.local)))
        else:
            sources.append(
                GitHubIssuesSource(
                    args.repo or os.environ.get("GITHUB_REPOSITORY", ""),
                    args.label,
                    os.environ.get("GITHUB_TOKEN"),
                    number_prefix=args.number_prefix,
                )
            )
    if args.links:
        links = load_all(Path(args.links)).links
    elif args.no_collect:
        links = []
    else:
        from traceability.collect import collect

        result = collect(Path(args.rootdir))
        links = [
            CaseLink(i["nodeid"], tuple(i["requirements"]))
            for i in result.items
            if i.get("requirements") or i.get("invalid")
        ]
        links += [
            CaseLink(i["nodeid"], tuple(b["id"] for b in i["invalid"]))
            for i in result.items
            if i.get("invalid")
        ]
    snapshot = Path(args.snapshot)
    # Diff against the last snapshot; without one, everything is new to it.
    previous = Registry.load(snapshot) if snapshot.is_file() else Registry()
    report = sync(sources, snapshot, links, previous=previous, write=args.write)
    if args.pr_body:
        from traceability._util import write_text

        write_text(Path(args.pr_body), report.to_markdown())
    if args.db:
        from traceability.store import save_registry, save_sync

        with open_db(args.db) as store:
            save_sync(store, report)
            if args.write:
                save_registry(store, Registry.load(snapshot))
    print(report.to_markdown())
    print("snapshot written" if report.written else "dry run: nothing written (pass --write)")
    return 0


def _requirements_show(args: argparse.Namespace) -> int:
    from traceability.requirements import Registry

    reg = Registry.load(rootdir=Path(args.rootdir))
    for req in reg:
        print(f"{req.id:10} {req.status:8} {req.priority or '-':7} {req.title}")
    print(f"{len(reg)} requirement(s) from {reg.path}")
    return 0


# ---------------------------------------------------------------------- pr comment (G-027)


@command
def _pr_comment(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("pr-comment", help="pull-request summary from run artefacts")
    p.add_argument("--reports", nargs="+", required=True, help="report directories (all jobs)")
    p.add_argument("--baseline", help="the latest main run's reports, for 'new' sections")
    p.add_argument("--out", default="reports/traceability/pr-comment.md")
    p.add_argument(
        "--post",
        action="store_true",
        help="upsert the PR comment (needs GITHUB_TOKEN; forks: step summary only)",
    )
    p.add_argument("--pr", type=int, help="PR number (default: from GITHUB_EVENT_PATH)")
    p.set_defaults(func=_pr_comment_run)


def _pr_comment_run(args: argparse.Namespace) -> int:
    import os

    from traceability._util import write_text
    from traceability.pr_comment import MARKER, collect_inputs, pr_number, render_pr_summary

    inputs = collect_inputs(
        [Path(d) for d in args.reports], Path(args.baseline) if args.baseline else None
    )
    body = render_pr_summary(inputs)
    write_text(Path(args.out), body)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(body)
    if not args.post:
        print(body)
        return 0
    number, fork = pr_number(os.environ.get("GITHUB_EVENT_PATH"))
    number = args.pr or number
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if fork:
        print("fork pull request: summary written to the step summary only")
        return 0
    if not (number and token and repo):
        print("not posted: needs a PR number, GITHUB_TOKEN and GITHUB_REPOSITORY")
        return 0
    from integrations.github import upsert_pr_comment

    ref = upsert_pr_comment(repo, number, body, MARKER, token)
    print(
        f"comment {'created' if ref and ref.created else 'updated'}: {ref.url}"
        if ref
        else "not posted (see the warning above); the step summary has the text"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m traceability", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for builder in COMMANDS:
        builder(sub)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
