"""The contract gate: a breaking change fails the build, and says what broke.

Two inputs, one answer (:class:`GateResult`):

* :func:`check_openapi` compares two OpenAPI documents (any provider language);
* :func:`check_contracts` compares the two newest stored consumer contracts.

``evidence`` is False when there was nothing to compare (no stored contract, an
unreadable spec). That is reported as ``no_evidence`` — never as compatible.

Every judged result can be recorded: one verdict line (kind ``contract``) in the
run's verdict file and one ``metric_contract_change`` row per change.

CLI (exit 0 compatible, 1 breaking, 2 no evidence)::

    python -m contract_testing.gate openapi OLD.json NEW.json
    python -m contract_testing.gate store --consumer web --provider api [--dir contracts]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from contract_testing.differ import ContractDiffer
from contract_testing.openapi_diff import BREAKING, NON_BREAKING, Change, diff_openapi
from contract_testing.storage import ContractStore

_LOCATION = re.compile(r"^\[(?P<loc>[^\]]+)\]\s*(?P<detail>.*)$")


@dataclass(frozen=True)
class GateResult:
    breaking: list[Change] = field(default_factory=list)
    non_breaking: list[Change] = field(default_factory=list)
    evidence: bool = True
    reason: str = ""
    from_version: str | None = None
    to_version: str | None = None

    @property
    def verdict(self) -> str:
        if not self.evidence:
            return "no_evidence"
        return "breaking" if self.breaking else "compatible"

    @property
    def exit_code(self) -> int:
        return {"compatible": 0, "breaking": 1, "no_evidence": 2}[self.verdict]

    def describe(self) -> str:
        if not self.evidence:
            return f"contract gate: NO EVIDENCE — {self.reason}"
        lines = [
            f"contract gate: {self.verdict.upper()} — {len(self.breaking)} breaking, "
            f"{len(self.non_breaking)} non-breaking"
        ]
        lines += [f"  - {c}" for c in self.breaking]
        return "\n".join(lines)


def _load(doc: str | Path | dict) -> dict:
    if isinstance(doc, dict):
        return doc
    text = Path(doc).read_text(encoding="utf-8")
    return json.loads(text) if str(doc).endswith(".json") else yaml.safe_load(text)


def check_openapi(old: str | Path | dict, new: str | Path | dict) -> GateResult:
    try:
        changes = diff_openapi(_load(old), _load(new))
    except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
        return GateResult(evidence=False, reason=f"cannot compare specs: {exc}")
    return GateResult(
        breaking=[c for c in changes if c.kind == BREAKING],
        non_breaking=[c for c in changes if c.kind == NON_BREAKING],
    )


def _change(kind: str, text: str, fallback: str) -> Change:
    match = _LOCATION.match(text)
    if match:
        return Change(kind, match.group("loc"), match.group("detail"), "contract")
    return Change(kind, fallback, text, "contract")


def check_contracts(store: ContractStore, consumer: str, provider: str) -> GateResult:
    """Diff the newest stored contract against the one before it."""
    versions = store.list_versions(consumer, provider)
    if not versions:
        return GateResult(evidence=False, reason=f"no stored contract for {consumer}/{provider}")
    if len(versions) == 1:
        return GateResult(to_version=versions[0])
    new = store.load_version(consumer, provider, versions[0])
    old = store.load_version(consumer, provider, versions[1])
    if new is None or old is None:
        return GateResult(evidence=False, reason="a stored contract version is unreadable")
    diff = ContractDiffer().diff(old, new)
    pair = f"{consumer}->{provider}"
    return GateResult(
        breaking=[_change(BREAKING, t, pair) for t in diff.breaking],
        non_breaking=[_change(NON_BREAKING, t, pair) for t in diff.non_breaking],
        from_version=old.version,
        to_version=new.version,
    )


def record(
    result: GateResult,
    *,
    consumer: str,
    provider: str,
    source: str,
    sink=None,
) -> str | None:
    """Write the verdict line and the change rows; returns a problem string or None."""
    from checks._sink import write_metrics
    from verification.verdicts import Kind, record_verdict

    subject = f"{consumer}->{provider}"
    record_verdict(
        Kind.CONTRACT,
        result.verdict,
        subject=subject,
        detail=result.reason or "; ".join(str(c) for c in result.breaking[:5]),
        nodeid=f"contract:{subject}",
    )
    rows: list[dict[str, Any]] = [
        {
            "consumer": consumer,
            "provider": provider,
            "from_version": result.from_version,
            "to_version": result.to_version,
            "change_type": change.kind,
            "location": change.location,
            "detail": change.detail,
            "source": source,
        }
        for change in [*result.breaking, *result.non_breaking]
    ]
    return write_metrics("metric_contract_change", rows, sink=sink) if rows else None


def main(argv: Sequence[str] | None = None, out=None) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog="python -m contract_testing.gate")
    sub = parser.add_subparsers(dest="command", required=True)
    spec = sub.add_parser("openapi", help="compare two OpenAPI documents")
    spec.add_argument("old")
    spec.add_argument("new")
    spec.add_argument("--consumer", default="clients")
    spec.add_argument("--provider", default="api")
    stored = sub.add_parser("store", help="compare the two newest stored contracts")
    stored.add_argument("--consumer", required=True)
    stored.add_argument("--provider", required=True)
    stored.add_argument("--dir", default="contracts")
    parser.add_argument("--no-record", action="store_true", help="print only")
    args = parser.parse_args(argv)

    if args.command == "openapi":
        result = check_openapi(args.old, args.new)
        source = "openapi"
    else:
        directory = Path(args.dir)
        if not directory.is_dir():
            result = GateResult(evidence=False, reason=f"{directory} does not exist")
        else:
            result = check_contracts(ContractStore(directory), args.consumer, args.provider)
        source = "capture"
    print(result.describe(), file=out)
    if not args.no_record:
        problem = record(result, consumer=args.consumer, provider=args.provider, source=source)
        if problem:
            print(f"contract gate metrics: DEGRADED ({problem})", file=out)
    return result.exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
