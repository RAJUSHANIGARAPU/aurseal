"""``assert_accessible(page)`` for use inside any UI test."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from checks._sink import MetricSink
from checks.a11y.axe import A11yReport, scan
from checks.a11y.judge import judge, manifest_hosts, record
from checks.a11y.policy import A11yPolicy
from verification.known_defects_manifest import ManifestEntry, load_manifest


def load_known(location: str | Path | None) -> list[ManifestEntry]:
    return load_manifest(location) if location else []


def assert_accessible(
    page: Any,
    policy: A11yPolicy | None = None,
    *,
    manifest: Iterable[ManifestEntry] = (),
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
    sink: MetricSink | None = None,
) -> A11yReport:
    """Scan ``page`` and fail on an unexplained blocking violation or a cleared known one.

    Also fails when axe did not run at all: "no violations" from a scan that evaluated
    no rules is not evidence.
    """
    entries = list(manifest)
    report = scan(page, policy, include=include, exclude=exclude, hosts=manifest_hosts(entries))
    if sink is not None:
        record([report], sink, entries)
    outcome = judge([report], policy, entries)
    if outcome.status != "pass":
        raise AssertionError(f"accessibility {outcome.status}: {outcome.reason}")
    return report
