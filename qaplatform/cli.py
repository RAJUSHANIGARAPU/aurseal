"""``python -m qaplatform``: run the server, manage tokens, push results from any CI.

Server-side commands open the store directly (``--db``, else ``QAP_DB``, else the results
store default). Client commands talk to a running server: ``QAP_URL``, ``QAP_TOKEN``,
``QAP_PROJECT`` (default ``default``), ``QAP_TIMEOUT_S``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from qaplatform.errors import PlatformError

DB_ENV = "QAP_DB"


def _store(args: argparse.Namespace) -> Any:
    from qaplatform.store import PlatformStore

    return PlatformStore.open(args.db or os.environ.get(DB_ENV) or None)


def service_factory(db: str | None) -> Any:
    """A factory that opens its own store (one per serving thread)."""

    def make() -> Any:
        from qaplatform import dispatch
        from qaplatform.readers import release_verdicts_from_env
        from qaplatform.service import PlatformService
        from qaplatform.store import PlatformStore

        return PlatformService(
            PlatformStore.open(db or os.environ.get(DB_ENV) or None),
            dispatcher=dispatch.from_env(),
            release_verdicts=release_verdicts_from_env(),
        )

    return make


def bootstrap(store: Any, *, user_id: str = "admin", ttl_days: int = 90) -> tuple[str, dict]:
    """Ensure the ``default`` project exists and issue one instance admin token."""
    from qaplatform.auth import tokens

    store.results.ensure_project("default", "Default project")
    store.ensure_user(user_id, instance_admin=True)
    return tokens.issue(
        store, user_id=user_id, project_id=None, role="admin", label="bootstrap", ttl_days=ttl_days
    )


def _client(args: argparse.Namespace) -> Any:
    from qaplatform.client import PlatformClient

    url = args.url or os.environ.get("QAP_URL")
    token = os.environ.get("QAP_TOKEN")
    if not url or not token:
        raise SystemExit("QAP_URL and QAP_TOKEN must be set (or pass --url)")
    return PlatformClient(
        url,
        token,
        args.project or os.environ.get("QAP_PROJECT") or "default",
        timeout_s=float(os.environ.get("QAP_TIMEOUT_S") or 30),
    )


def _print(doc: Any) -> None:
    print(json.dumps(doc, indent=2, sort_keys=True))


def cmd_serve(args: argparse.Namespace) -> int:
    from qaplatform.http.app import serve

    _store(args).close()  # migrate once before serving
    serve(service_factory(args.db), host=args.host, port=args.port)
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    store = _store(args)
    raw, row = bootstrap(store, user_id=args.user, ttl_days=args.ttl_days)
    store.close()
    print(f"admin token (shown once, store it in a secret store): {raw}")
    print(f"token id: {row['id']}  expires: {row['expires_at']}")
    return 0


def _operator_service(args: argparse.Namespace) -> Any:
    from qaplatform.service import PlatformService

    return PlatformService(_store(args))


def cmd_token_create(args: argparse.Namespace) -> int:
    from qaplatform.auth.tokens import local_operator

    svc = _operator_service(args)
    op = local_operator()
    try:
        if svc.store.binding(args.user, args.project) is None:
            svc.grant_member(
                op,
                args.project,
                {"schema_version": "1.0", "user_id": args.user, "role": args.role},
            )
        doc = svc.issue_token(
            op,
            args.project,
            {
                "schema_version": "1.0",
                "user_id": args.user,
                "role": args.role,
                "label": args.label,
                "ttl_days": args.ttl_days,
            },
        )
    finally:
        svc.store.close()
    print(f"token (shown once): {doc['token']}")
    print(f"token id: {doc['id']}  role: {doc['role']}  expires: {doc['expires_at']}")
    return 0


def cmd_token_list(args: argparse.Namespace) -> int:
    store = _store(args)
    _print(store.tokens(None if args.instance else args.project))
    store.close()
    return 0


def cmd_token_revoke(args: argparse.Namespace) -> int:
    from qaplatform.auth.tokens import local_operator
    from results._util import utc_now

    svc = _operator_service(args)
    try:
        row = svc.store.token(args.token_id)
        if row is None or row["revoked_at"]:
            print("no active token with that id")
            return 1
        if row["project_id"] is None:  # an instance token has no project chain to audit
            svc.store.revoke_token(args.token_id, utc_now())
        else:
            svc.revoke_token(local_operator(), row["project_id"], args.token_id)
    finally:
        svc.store.close()
    print("revoked")
    return 0


def cmd_project_create(args: argparse.Namespace) -> int:
    from qaplatform.auth.tokens import local_operator

    svc = _operator_service(args)
    try:
        doc = svc.create_project(
            local_operator(), {"schema_version": "1.0", "key": args.key, "name": args.name}
        )
    finally:
        svc.store.close()
    _print(doc)
    return 0


def cmd_project_list(args: argparse.Namespace) -> int:
    from qaplatform.auth.tokens import local_operator

    svc = _operator_service(args)
    try:
        _print(svc.list_projects(local_operator()))
    finally:
        svc.store.close()
    return 0


def _audit_scope(args: argparse.Namespace) -> tuple[Any, Any]:
    store = _store(args)
    if store.project(args.project) is None:
        store.close()
        raise SystemExit(f"no project {args.project!r}")
    return store, store.scoped(args.project)


def cmd_audit_verify(args: argparse.Namespace) -> int:
    from qaplatform import audit

    store, scoped = _audit_scope(args)
    report = audit.verify_chain(scoped)
    store.close()
    _print(report.to_json())
    return 0 if report.ok else 1


def cmd_audit_head(args: argparse.Namespace) -> int:
    from qaplatform import audit

    store, scoped = _audit_scope(args)
    _print(audit.head(scoped))
    store.close()
    return 0


def cmd_audit_export(args: argparse.Namespace) -> int:
    from qaplatform import audit

    store, scoped = _audit_scope(args)
    count = audit.export_jsonl(scoped, args.out)
    store.close()
    print(f"{count} audit row(s) written to {args.out}")
    return 0


def _session_id(args: argparse.Namespace) -> str:
    sid = args.session or os.environ.get("QAP_SESSION")
    if not sid:
        raise SystemExit("pass --session or set QAP_SESSION")
    return sid


def cmd_session_start(args: argparse.Namespace) -> int:
    doc = _client(args).start_session(
        args.charter, area=args.area, requirements=args.req or None, timebox_min=args.timebox
    )
    print(f"session {doc['id']} started (run {doc['run_id']}); export QAP_SESSION={doc['id']}")
    return 0


def cmd_session_note(args: argparse.Namespace) -> int:
    kind = "bug" if args.defect and args.kind == "observation" else args.kind
    doc = _client(args).session_note(
        _session_id(args),
        {"kind": kind, "text": args.text, "defect_key": args.defect, "attachment_uri": args.attach},
    )
    print(f"note {doc['seq']} ({doc['kind']})")
    return 0


def cmd_session_end(args: argparse.Namespace) -> int:
    doc = _client(args).end_session(_session_id(args), args.debrief, abandoned=args.abandon)
    over = " (over the timebox)" if doc["over_timebox"] else ""
    print(
        f"session {doc['id']} {doc['status']} after {doc['duration_min']} min{over}; "
        f"{doc['note_counts']['bug']} bug note(s)"
    )
    return 0


def cmd_session_capture(args: argparse.Namespace) -> int:
    from qaplatform.capture import capture_in_browser

    posted = capture_in_browser(_client(args), _session_id(args), args.target, headless=False)
    print(f"capture posted {len(posted)} note(s)")
    return 0


def cmd_push_junit(args: argparse.Namespace) -> int:
    client = _client(args)
    run = client.create_run(args.name, external_id=args.external_id, release=args.release)
    reports = [client.push_junit(run["run_id"], f) for f in args.files]
    out: dict[str, Any] = {"run_id": run["run_id"], "files": reports}
    if args.finish:
        out["summary"] = client.finish_run(run["run_id"])
    _print(out)
    return 0


def cmd_push_results(args: argparse.Namespace) -> int:
    client = _client(args)
    doc = json.loads(Path(args.file).read_text(encoding="utf-8"))
    results = doc["results"] if isinstance(doc, dict) else doc
    _print(client.push_results(args.run, results))
    return 0


def cmd_push_verdicts(args: argparse.Namespace) -> int:
    _print(_client(args).push_verdicts(args.run, args.file))
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    doc = _client(args).health()
    _print(doc)
    return 0 if doc.get("status") == "ready" else 1


def cmd_validate(args: argparse.Namespace) -> int:
    from qaplatform import schemas

    errors = schemas.errors_for(args.schema, json.loads(Path(args.file).read_text("utf-8")))
    for err in errors:
        print(f"{err['pointer'] or '(root)'}: {err['message']}")
    print("valid" if not errors else f"{len(errors)} violation(s)")
    return 0 if not errors else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m qaplatform")
    sub = parser.add_subparsers(dest="command", required=True)

    def server(name: str, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--db", help=f"store path (default ${DB_ENV}, else the results store)")
        return p

    def client(name: str, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--url", help="server base URL (default $QAP_URL)")
        p.add_argument("--project", help="project key (default $QAP_PROJECT or 'default')")
        return p

    p = server("serve", "run the API server (needs requirements-platform.txt)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(func=cmd_serve)

    p = server("init", "create the default project and print one admin token")
    p.add_argument("--user", default="admin")
    p.add_argument("--ttl-days", type=int, default=90)
    p.set_defaults(func=cmd_init)

    token = sub.add_parser("token", help="API tokens").add_subparsers(dest="token", required=True)
    p = token.add_parser("create", help="issue a project token (printed once)")
    p.add_argument("--db")
    p.add_argument("--project", default="default")
    p.add_argument("--role", default="tester", choices=("viewer", "tester", "lead", "admin"))
    p.add_argument("--user", default="ci")
    p.add_argument("--label", default="")
    p.add_argument("--ttl-days", type=int, default=90)
    p.set_defaults(func=cmd_token_create)
    p = token.add_parser("list", help="tokens of a project (never the secrets)")
    p.add_argument("--db")
    p.add_argument("--project", default="default")
    p.add_argument("--instance", action="store_true", help="list instance tokens instead")
    p.set_defaults(func=cmd_token_list)
    p = token.add_parser("revoke", help="revoke a token by id")
    p.add_argument("--db")
    p.add_argument("token_id")
    p.set_defaults(func=cmd_token_revoke)

    project_cmd = sub.add_parser("project", help="projects (tenants)").add_subparsers(
        dest="project_cmd", required=True
    )
    p = project_cmd.add_parser("create", help="create a project; you become its admin")
    p.add_argument("--db")
    p.add_argument("key")
    p.add_argument("--name", default="")
    p.set_defaults(func=cmd_project_create)
    p = project_cmd.add_parser("list", help="every project")
    p.add_argument("--db")
    p.set_defaults(func=cmd_project_list)

    audit_cmd = sub.add_parser("audit", help="the audit log").add_subparsers(
        dest="audit", required=True
    )
    for name, func, text in (
        ("verify", cmd_audit_verify, "recompute the hash chain (exit 1 when broken)"),
        ("head", cmd_audit_head, "print the chain head, to anchor it elsewhere"),
        ("export", cmd_audit_export, "write the chain as JSONL"),
    ):
        p = audit_cmd.add_parser(name, help=text)
        p.add_argument("--db")
        p.add_argument("--project", default="default")
        if name == "export":
            p.add_argument("--out", required=True)
        p.set_defaults(func=func)

    session = sub.add_parser("session", help="exploratory sessions").add_subparsers(
        dest="session_cmd", required=True
    )

    def session_parser(name: str, help_text: str) -> argparse.ArgumentParser:
        sp = session.add_parser(name, help=help_text)
        sp.add_argument("--url", help="server base URL (default $QAP_URL)")
        sp.add_argument("--project", help="project key (default $QAP_PROJECT or 'default')")
        if name != "start":
            sp.add_argument("--session", help="session id (default $QAP_SESSION)")
        return sp

    p = session_parser("start", "start a session")
    p.add_argument("--charter", required=True)
    p.add_argument("--area", default="")
    p.add_argument("--req", action="append", help="requirement key (repeatable)")
    p.add_argument("--timebox", type=int, help="minutes")
    p.set_defaults(func=cmd_session_start)
    p = session_parser("note", "add a note")
    p.add_argument("text")
    p.add_argument(
        "--kind", default="observation", choices=("observation", "question", "bug", "idea")
    )
    p.add_argument("--defect", help="defect key (makes it a bug note)")
    p.add_argument("--attach", help="attachment path or URL")
    p.set_defaults(func=cmd_session_note)
    p = session_parser("end", "end the session")
    p.add_argument("--debrief", default="")
    p.add_argument("--abandon", action="store_true")
    p.set_defaults(func=cmd_session_end)
    p = session_parser("capture", "open a browser on URL and capture until it is closed")
    p.add_argument("target", metavar="URL")
    p.set_defaults(func=cmd_session_capture)

    p = client("push-junit", "create a run and upload JUnit XML files")
    p.add_argument("files", nargs="+")
    p.add_argument("--name", required=True)
    p.add_argument("--external-id")
    p.add_argument("--release")
    p.add_argument("--finish", action="store_true", help="finish the run after uploading")
    p.set_defaults(func=cmd_push_junit)

    p = client("push-results", "upload a results JSON file (a batch body or a list)")
    p.add_argument("file")
    p.add_argument("--run", required=True)
    p.set_defaults(func=cmd_push_results)

    p = client("push-verdicts", "upload a verdicts.jsonl file")
    p.add_argument("file")
    p.add_argument("--run", required=True)
    p.set_defaults(func=cmd_push_verdicts)

    p = client("health", "is the server ready (exit 0) or not (exit 1)")
    p.set_defaults(func=cmd_health)

    p = sub.add_parser("validate", help="validate a JSON payload against a schema offline")
    p.add_argument("schema")
    p.add_argument("file")
    p.set_defaults(func=cmd_validate)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except PlatformError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


__all__ = ["bootstrap", "build_parser", "main", "service_factory"]
