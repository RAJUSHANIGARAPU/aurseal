"""``python -m tools.config_preflight``: the command line over the preflight and its sources.

Exit status: 0 clean; 2 with ``--fail-on-reject`` when a record is rejected,
orphaned, stuck or on a stale action/route (or not assessed, unless
``--allow-not-assessed``); 1 undetermined: bad input, a source that failed, a
type with no config at the ref, or nothing scanned at all. A finding wins over
an incomplete scan; the report lists both.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from gate.read_only import ReadOnlyClient
from tools.config_preflight import sources as src
from tools.config_preflight.config_preflight import (
    Config,
    FileRecordReader,
    InputError,
    Preflight,
    RecordShape,
    apply_migration_hints,
    load_config,
    load_config_at_ref,
    render_markdown,
)

TOKEN_ENV_DEFAULT = "CONFIG_PREFLIGHT_TOKEN"


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m tools.config_preflight",
        description="Which stored records would the new workflow config reject?",
    )
    c = p.add_argument_group("config")
    c.add_argument("--old", help="old config: workflow file or directory")
    c.add_argument("--new", help="new config: workflow file or directory")
    c.add_argument("--old-ref", help="git ref of the old config (optional)")
    c.add_argument("--new-ref", help="git ref of the new config")
    c.add_argument("--config", help="one config path (file or directory) read at the refs")
    c.add_argument(
        "--config-template",
        action="append",
        default=[],
        help="per-type config path with {type}, tried in order (repeatable)",
    )
    c.add_argument("--repo", default=".", help="git repository for the refs (default .)")
    c.add_argument(
        "--migration-template",
        action="append",
        default=[],
        help="per-type migration script path with {type}, read at --new-ref (repeatable)",
    )
    s = p.add_argument_group("scope")
    s.add_argument("--types", help="comma-separated entity types; overrides --manifest")
    s.add_argument("--manifest", help="PATH@REF of a manifest listing the types to check")
    s.add_argument("--target", help="the manifest target whose types to check")
    r = p.add_argument_group("records")
    r.add_argument("--records", help="records export: .jsonl or .json")
    r.add_argument("--base-url", help="read the paged lists from this service")
    r.add_argument("--offline-fixtures", help="answer every GET and config read from DIR")
    r.add_argument("--lists", default="records,drafts", help="paged lists to read per type")
    for name, default in src.DEFAULT_PATHS.items():
        r.add_argument(f"--{name}-path", default=default, help=f"default {default}")
    r.add_argument("--page-size", type=int, default=200)
    r.add_argument("--events", action="store_true", help="read the action-entered history")
    r.add_argument("--from", dest="date_from", type=dt.date.fromisoformat, help="first UTC day")
    r.add_argument("--to", dest="date_to", type=dt.date.fromisoformat, help="last UTC day")
    r.add_argument("--workers", type=int, default=4, help="event days read in parallel")
    r.add_argument("--ids", help="file of record ids ('<id>' or '<type> <id>' per line)")
    r.add_argument("--token-env", default=TOKEN_ENV_DEFAULT, help="env var holding the token")
    r.add_argument("--token-file", help="file holding the token (wins over --token-env)")
    k = p.add_argument_group("record shape")
    for key, default in (
        ("id", "id"),
        ("type", "type"),
        ("state", "state"),
        ("fields", "fields"),
        ("action", "action"),
        ("target", "target"),
    ):
        k.add_argument(f"--{key}-key", default=default)
    o = p.add_argument_group("output")
    o.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    o.add_argument(
        "--fail-on-reject",
        action="store_true",
        help="exit 2 on any finding (rejected, orphaned, stuck, stale, not assessed)",
    )
    o.add_argument(
        "--allow-not-assessed",
        action="store_true",
        help="with --fail-on-reject: do not fail on not-assessed rules alone",
    )
    return p.parse_args(argv)


def _config_source(args: argparse.Namespace) -> src.GitSource | src.FixtureConfigSource:
    if args.offline_fixtures:
        return src.FixtureConfigSource(args.offline_fixtures)
    return src.GitSource(args.repo)


def _load_side(
    args: argparse.Namespace,
    which: str,
    source: src.GitSource | src.FixtureConfigSource,
    types: list[str] | None,
) -> tuple[Config | None, str, list[str]]:
    """(config or None, label, could-not-determine reasons) for ``old`` or ``new``."""
    path, ref = getattr(args, which), getattr(args, f"{which}_ref")
    if path and ref:
        raise InputError(f"give one of --{which} and --{which}-ref, not both")
    if path:
        return load_config(path), Path(path).name, []
    if not ref:
        return None, "", []
    if args.config_template:
        if not types:
            raise InputError("--config-template needs --types or --manifest")
        config, missing = src.load_typed_config(source, ref, types, args.config_template)
        return config, ref, missing
    if not args.config:
        raise InputError(f"--{which}-ref needs --config or --config-template")
    if isinstance(source, src.FixtureConfigSource):
        if source.read(ref, args.config) is None and not (source.root / ref / args.config).is_dir():
            raise InputError(f"no config at {ref}:{args.config}")
        return load_config(source.root / ref / args.config), ref, []
    return load_config_at_ref(args.repo, ref, args.config), ref, []


def _types(
    args: argparse.Namespace, source: src.GitSource | src.FixtureConfigSource
) -> list[str] | None:
    if args.types:
        return [t.strip() for t in args.types.split(",") if t.strip()]
    if args.manifest:
        return src.read_manifest(source, args.manifest, args.target)
    return None


def _client(args: argparse.Namespace) -> ReadOnlyClient | None:
    if args.offline_fixtures:
        return ReadOnlyClient(src.FixtureHttp(args.offline_fixtures).get)
    if args.base_url:
        return src.live_client(args.base_url, src.read_token(args.token_env, args.token_file))
    return None


def build(args: argparse.Namespace) -> Preflight:
    source = _config_source(args)
    types = _types(args, source)
    new, new_label, missing = _load_side(args, "new", source, types)
    if new is None:
        raise InputError("give --new or --new-ref")
    old, old_label, _ = _load_side(args, "old", source, types)
    shape = RecordShape(
        args.id_key, args.type_key, args.state_key, args.fields_key, args.action_key,
        args.target_key,
    )  # fmt: skip
    pre = Preflight(
        old, new, shape=shape, old_label=old_label or "(none)", new_label=new_label, types=types
    )
    folded = {k.casefold(): k for k in new}
    if types is not None and not args.config_template:
        missing += [
            f"{t}: could not determine, no config for it in {new_label}"
            for t in types
            if t.casefold() not in folded
        ]
    pre.result.undetermined.extend(missing)
    # the config's own spelling from here on: paths and labels use the entity key
    scan_types = list(
        dict.fromkeys(folded[t.casefold()] for t in types or new if t.casefold() in folded)
    )

    if args.records:
        for record in FileRecordReader(Path(args.records)).records():
            pre.check(record, "file")

    client = _client(args)
    if client is None and (args.events or args.ids):
        raise InputError("--events and --ids need --base-url or --offline-fixtures")
    if client is not None:
        days = None
        if args.events:
            if not (args.date_from and args.date_to):
                raise InputError("--events needs --from and --to")
            days = src.day_windows(args.date_from, args.date_to)
        paths = {name: getattr(args, f"{name}_path") for name in src.DEFAULT_PATHS}
        lists = tuple(x.strip() for x in args.lists.split(",") if x.strip())
        unknown = set(lists) - {"records", "drafts"}
        if unknown:
            raise InputError(f"--lists: unknown list(s) {sorted(unknown)}")
        plan = src.ScanPlan(
            types=scan_types,
            paths=paths,
            lists=lists,
            page_size=args.page_size,
            days=days,
            workers=args.workers,
            ids=src.read_ids(args.ids) if args.ids else None,
        )
        src.scan(pre, client, plan)

    if args.migration_template:
        if not args.new_ref:
            raise InputError("--migration-template reads at --new-ref")
        scripts = src.read_migration_scripts(
            source, args.new_ref, scan_types, args.migration_template
        )
        apply_migration_hints(pre.result, scripts)
    return pre


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        pre = build(args)
    except InputError as exc:
        print(f"config-preflight: {exc}", file=sys.stderr)
        return 1
    result = pre.result
    if args.json:
        print(json.dumps(result.to_json(), indent=2, default=str))
    else:
        print(render_markdown(result), end="")
    return result.exit_code(
        fail_on_findings=args.fail_on_reject, allow_not_assessed=args.allow_not_assessed
    )
