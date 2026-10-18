"""JUnit XML -> ``test-result`` payloads.

The XML is read by :mod:`results.io.junit` (DOCTYPE/ENTITY refused before parsing,
``defusedxml`` used when installed) so there is one JUnit reader in the repository. The
output is the same payload a native JSON reporter sends, so both paths share one write
path and one validation.

Mapping: ``classname`` + ``name`` -> ``test_id`` (``<classname>::<name>``; pytest XML maps
back to node ids); ``<failure>``/``<error>``/``<skipped>``/none -> failed/error/skipped/passed;
``<property name="requirement" value="REQ-12"/>`` (repeatable) -> ``requirements``;
``defect``/``known_defect`` -> ``tickets``; ``[[ATTACHMENT|path]]`` -> evidence links.
"""

from __future__ import annotations

import re
from typing import Any

from qaplatform.errors import BadRequest, TooLarge

MAX_BYTES = 50_000_000
_REQUIREMENT = re.compile(r"^[A-Z][A-Z0-9]+-[0-9]+$")


def parse_junit(
    xml: bytes, *, max_bytes: int = MAX_BYTES
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(results, rejected)``: result payloads, plus test cases or links that were refused."""
    from results.io.junit import JUnitError
    from results.io.junit import parse_junit as records_from_junit

    if len(xml) > max_bytes:
        raise TooLarge(f"JUnit body is {len(xml)} bytes, the limit is {max_bytes}")
    rejected: list[dict[str, Any]] = []
    try:
        records = list(records_from_junit(xml, rejected=rejected))
    except (JUnitError, ValueError) as exc:
        raise BadRequest(f"JUnit rejected: {exc}") from exc
    cases = {r.data["case_id"]: r.data for r in records if r.type == "case"}
    evidence: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        if rec.type == "evidence" and rec.data.get("nodeid"):
            evidence.setdefault(rec.data["nodeid"], []).append(
                {"kind": rec.data["kind"], "uri": rec.data["uri"]}
            )
    out: list[dict[str, Any]] = []
    for rec in records:
        if rec.type != "result":
            continue
        r = rec.data
        case = cases.get(r.get("case_id") or "", {})
        links = [tuple(x) for x in case.get("links") or ()]
        requirements = []
        for kind, ref in links:
            if kind != "requirement":
                continue
            if _REQUIREMENT.match(ref):
                requirements.append(ref)
            else:
                rejected.append({"nodeid": r["nodeid"], "reason": f"requirement {ref!r} ignored"})
        tickets = [ref for kind, ref in links if kind == "defect"]
        item: dict[str, Any] = {"test_id": r["nodeid"], "outcome": r["outcome"]}
        optional = {
            "title": case.get("title"),
            "suite": case.get("module"),
            "duration_ms": r.get("duration_ms"),
            "message": (r.get("message") or "")[:4000] or None,
            "trace": (r.get("longrepr") or "")[:65536] or None,
            "skip_reason": (r.get("skip_reason") or "")[:1000] or None,
            "requirements": requirements or None,
            "tickets": tickets or None,
            "evidence": evidence.get(r["nodeid"]) or None,
        }
        item.update({k: v for k, v in optional.items() if v is not None})
        out.append(item)
    return out, rejected


__all__ = ["MAX_BYTES", "parse_junit"]
