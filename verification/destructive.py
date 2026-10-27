"""Refusal controls for a destructive endpoint: what a delete must refuse, and that it did.

A green "delete works" says nothing about the refusals that make a delete
safe. :func:`delete_contract` walks one item through the whole contract, in an
order where every step's precondition is the previous step's outcome:

=====================  ============================  ===========================
step                   expected                      otherwise
=====================  ============================  ===========================
anonymous delete       401 / 403, item still exists  finding (and stop if gone)
impossible id          404                           403 naming the permission:
                                                     no verdict; else a finding
ownership guard        no refusal                    no verdict (not ours)
real delete            2xx                           403: no verdict; else finding
read back              item gone                     finding
repeat delete          404                           finding
=====================  ============================  ===========================

A caller without the permission is reported through ``no_verdict`` ("caller
lacks permission"), never as a pass: a 403 on every call would otherwise look
exactly like a delete that refuses correctly.

The callables are thin: ``delete(id)`` and ``delete_anon(id)`` return an
:class:`~api.clients.http_engine.ApiResponse`; ``exists(id)`` re-reads the
item. ``guard`` is the ownership check from :mod:`test_data.ownership` run on a
fresh read right before the real delete; a refusal there abstains.
"""

from __future__ import annotations

from collections.abc import Callable, Collection

from api.clients.http_engine import ApiResponse
from verification.evidence_verdict import EvidenceVerdict

ANONYMOUS_REFUSALS = frozenset({401, 403})


def _permission_refusal(response: ApiResponse, hint: str | None) -> bool:
    return response.status == 403 and (hint is None or hint.lower() in response.text.lower())


def delete_contract(
    delete: Callable[[str], ApiResponse],
    delete_anon: Callable[[str], ApiResponse],
    exists: Callable[[str], bool],
    *,
    item_id: str,
    impossible_id: str,
    guard: Callable[[], str | None] | None = None,
    permission_hint: str | None = None,
    anonymous_refusals: Collection[int] = ANONYMOUS_REFUSALS,
    subject: str = "delete contract",
    verdict: EvidenceVerdict | None = None,
) -> list[str]:
    """Run every step; return the findings (empty when the contract holds).

    Abstains through ``verdict.no_verdict`` when the caller lacks the
    permission or the ownership guard refuses the item.
    """
    if item_id == impossible_id:
        raise ValueError("the impossible id must differ from the item under test")
    coverage = verdict or EvidenceVerdict(subject)
    findings: list[str] = []

    anon = delete_anon(item_id)
    if anon.status not in anonymous_refusals:
        findings.append(f"anonymous delete of {item_id} answered {anon}, expected 401/403")
    if not exists(item_id):
        findings.append(f"{item_id} no longer exists after the anonymous delete")
        return findings

    impossible = delete(impossible_id)
    if _permission_refusal(impossible, permission_hint):
        coverage.no_verdict(f"caller lacks permission: {impossible}")
    if impossible.status != 404:
        findings.append(f"delete of impossible id {impossible_id} answered {impossible}, not 404")

    if guard is not None:
        refusal = guard()
        if refusal:
            coverage.no_verdict(f"ownership guard refused to delete {item_id}: {refusal}")

    real = delete(item_id)
    if _permission_refusal(real, permission_hint):
        coverage.no_verdict(f"caller lacks permission: {real}")
    if not real.is_success:
        findings.append(f"delete of {item_id} answered {real}, expected 2xx")
        return findings
    if exists(item_id):
        findings.append(f"{item_id} still exists after a successful delete")

    repeat = delete(item_id)
    if repeat.status != 404:
        findings.append(f"repeat delete of {item_id} answered {repeat}, expected 404")
    if not findings:
        coverage.proved(
            f"{item_id}: anonymous {anon.status} and kept, impossible id 404, deleted "
            f"{real.status} and gone, repeat 404"
        )
    return findings
