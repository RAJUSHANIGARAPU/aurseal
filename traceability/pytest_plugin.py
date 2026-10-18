"""``requirement`` marker: link a test to the requirements it covers.

    @pytest.mark.requirement("REQ-12", "REQ-13")
    def test_renewal_date_follows_the_term(): ...

Valid on functions, classes and modules (``pytestmark``); ids from every level are merged.
Each id is checked twice: its shape (``REQUIREMENT_ID_PATTERN`` and a requirement prefix
from ``REQUIREMENT_ID_PREFIXES``) and its state in the registry (``requirements/``).
A bad id is a warning by default; with ``REQUIREMENTS_STRICT=true`` (the CI setting) the
test fails at setup, naming the id, so a typo cannot point at nothing.

Ids leave the run three ways:

* ``item.user_properties["requirements"]`` (comma separated; reaches JUnit XML);
* an Allure link of type ``requirement`` (``REQUIREMENT_URL_PATTERN`` such as
  ``https://tracker.example.test/{}`` builds the URL, else Allure's
  ``--allure-link-pattern requirement:...`` does);
* ``reports/traceability/case-links.json``, written once by the controller.

A coverage verdict whose ticket (or subject's first word) is a requirement id is an extra
link of origin ``coverage_verdict``. When the test also carries markers and the two
disagree, the file lists a mismatch; nothing is merged silently.

Registered from the root ``conftest.py``; elsewhere ``-p traceability.pytest_plugin``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import pytest

from traceability._util import env_flag
from traceability.links import CaseLink, CaseLinks, InvalidLink, links_path, write_case_links
from traceability.requirements import (
    STRICT_ENV,
    Namespace,
    Registry,
    RequirementId,
    ids_in_verdict,
)

logger = logging.getLogger(__name__)

MARKER = "requirement"
COVERS_MARKER = "covers"
DRAFT_MARKER = "draft"
_DRAFT_KEYS = ("generator", "source")
PLUGIN_NAME = "traceability-requirements"
PROPERTY = "requirements"
URL_PATTERN_ENV = "REQUIREMENT_URL_PATTERN"
LANE_ENV = "RUN_TRUST_LANE"
REPORT_ATTR = "trace_links"

_invalid_key = pytest.StashKey[list[InvalidLink]]()


def marker_ids(item: pytest.Item) -> list[str]:
    """Every id on the item's ``requirement`` markers (function, class, module), sorted."""
    found: set[str] = set()
    for mark in item.iter_markers(MARKER):
        found.update(str(arg).strip() for arg in mark.args)
    return sorted(found, key=natural_key)


def natural_key(text: str) -> tuple:
    """``REQ-2`` before ``REQ-10``."""
    head, _, tail = text.partition("-")
    return (head, int(tail)) if tail.isdigit() else (head, 0, tail)


def _case_id(item: pytest.Item) -> str:
    pin = item.get_closest_marker("case_id")
    if pin is not None and pin.args:
        return str(pin.args[0])
    from results.catalogue import case_id_for_nodeid

    return case_id_for_nodeid(item.nodeid)


class RequirementPlugin:
    def __init__(
        self,
        registry: Registry,
        strict: bool,
        *,
        namespace: Namespace | None = None,
        out_path: Path | None = None,
        run_id: str = "",
    ) -> None:
        self.registry = registry
        self.strict = strict
        self.namespace = namespace or Namespace.from_env()
        self.out_path = out_path
        self.run_id = run_id
        self._verdict_offset = 0
        self._seen: dict[str, dict[str, Any]] = {}
        self._extra: list[Any] = []  # later features add per-item attributes here

    # ------------------------------------------------------------- per item

    def check(self, item: pytest.Item) -> tuple[list[RequirementId], list[InvalidLink]]:
        good: list[RequirementId] = []
        bad: list[InvalidLink] = []
        for raw in marker_ids(item):
            problem = self.namespace.problem(raw)
            if problem is None:
                state = self.registry.status(raw)
                problem = None if state == "active" else state
            if problem is None:
                good.append(RequirementId(raw))
            else:
                bad.append(InvalidLink(item.nodeid, raw, problem))
        return good, bad

    def links_for(self, item: pytest.Item) -> tuple[RequirementId, ...]:
        return tuple(self.check(item)[0])

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, config: pytest.Config, items: list[pytest.Item]):
        url_pattern = os.environ.get(URL_PATTERN_ENV, "").strip()
        for item in items:
            good, bad = self.check(item)
            if good:
                item.user_properties.append((PROPERTY, ",".join(good)))
                for req in good:
                    url = url_pattern.format(req) if url_pattern else req
                    item.add_marker(pytest.mark.allure_link(url, link_type=MARKER, name=req))
            if bad:
                item.stash[_invalid_key] = bad
                if not self.strict:
                    for b in bad:
                        item.warn(pytest.PytestWarning(_explain(b)))

    def pytest_runtest_setup(self, item: pytest.Item) -> None:
        bad = item.stash.get(_invalid_key, None)
        if bad and self.strict:
            pytest.fail(
                "; ".join(_explain(b) for b in bad) + f" ({STRICT_ENV} is set)", pytrace=False
            )

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_makereport(self, item: pytest.Item, call: pytest.CallInfo[None]):
        report = yield
        if call.when == "setup":
            entry = self.item_entry(item)
            if entry:
                setattr(report, REPORT_ATTR, entry)
        return report

    def item_entry(self, item: pytest.Item) -> dict[str, Any]:
        """What the controller needs about one item; empty when there is nothing to say."""
        good, bad = self.check(item)
        entry: dict[str, Any] = {}
        if good:
            entry["requirements"] = list(good)
        if bad:
            entry["invalid"] = [b.to_json() for b in bad]
        draft = item.get_closest_marker(DRAFT_MARKER)
        if draft is not None:
            entry["review_state"] = "draft"
            entry.update({k: str(v) for k, v in draft.kwargs.items() if k in _DRAFT_KEYS})
        covers = sorted({str(a) for m in item.iter_markers(COVERS_MARKER) for a in m.args})
        if covers:
            entry["covers"] = covers
        for extra in self._extra:
            entry.update(extra(item))
        if entry:
            entry["case_id"] = _case_id(item)
        return entry

    def add_item_attributes(self, provider) -> None:
        """``provider(item) -> dict`` adds keys to an item's entry (review state, covers...)."""
        self._extra.append(provider)

    # ------------------------------------------------------------- controller

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        from verification.verdicts import file_size

        self._verdict_offset = file_size()

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        entry = getattr(report, REPORT_ATTR, None)
        if report.when == "setup" and isinstance(entry, dict):
            self._seen[report.nodeid] = entry

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        if hasattr(session.config, "workerinput") or self.out_path is None:
            return
        if session.config.option.collectonly:
            return
        try:
            write_case_links(self.build(), self.out_path)
        except Exception as exc:  # reporting must never change the run's outcome
            logger.error("[traceability] could not write %s: %s", self.out_path, exc)

    def build(self) -> CaseLinks:
        from verification.verdicts import read_verdicts

        doc = CaseLinks(run_id=self.run_id)
        for nodeid, entry in sorted(self._seen.items()):
            case_id = entry.get("case_id")
            reqs = tuple(entry.get("requirements") or ())
            if reqs:
                doc.links.append(CaseLink(nodeid, reqs, "marker", case_id))
            for bad in entry.get("invalid") or ():
                doc.invalid.append(InvalidLink(nodeid, bad["id"], bad["problem"]))
            extra = {k: v for k, v in entry.items() if k not in ("requirements", "invalid")}
            if extra:
                doc.tests[nodeid] = extra
        by_test: dict[str, list[str]] = {}
        for record in read_verdicts(start=self._verdict_offset):
            if record.get("kind") != "coverage" or not record.get("nodeid"):
                continue
            if self.run_id and record.get("run_id") not in ("", self.run_id):
                continue
            for req in ids_in_verdict(record, self.namespace):
                bucket = by_test.setdefault(str(record["nodeid"]), [])
                if req not in bucket:
                    bucket.append(req)
        for nodeid, reqs in sorted(by_test.items()):
            case_id = (self._seen.get(nodeid) or {}).get("case_id")
            doc.links.append(CaseLink(nodeid, tuple(reqs), "coverage_verdict", case_id))
            marked = list((self._seen.get(nodeid) or {}).get("requirements") or ())
            if marked and set(marked) != set(reqs):
                doc.mismatches.append({"nodeid": nodeid, "marker": marked, "verdict": reqs})
        return doc


def _explain(bad: InvalidLink) -> str:
    why = {
        "format": "is not shaped like a requirement id",
        "prefix": "has a prefix that is not a requirement prefix (a defect id?)",
        "unknown": "is not in the requirements registry",
        "retired": "is retired in the requirements registry",
    }[bad.problem]
    return f"{bad.nodeid}: requirement {bad.id!r} {why}"


def register(config: pytest.Config) -> RequirementPlugin:
    config.addinivalue_line(
        "markers",
        f"{MARKER}(*ids): the requirements this test covers ({STRICT_ENV}=true fails bad ids)",
    )
    config.addinivalue_line(
        "markers",
        f"{COVERS_MARKER}(*items): API operations ('GET /path/{{id}}') or spec scenarios "
        f"('spec:<file>#<scenario>') this test covers, for the coverage-gap report",
    )
    config.addinivalue_line(
        "markers",
        f"{DRAFT_MARKER}(generator, source, generated_at): generated test awaiting review; "
        f"excluded from the gate and from evidence until a reviewed PR removes it",
    )
    existing = config.pluginmanager.get_plugin(PLUGIN_NAME)
    if existing is not None:
        return existing
    rootdir = Path(str(config.rootpath))
    try:
        registry = Registry.load(rootdir=rootdir)
    except ValueError as exc:
        raise pytest.UsageError(f"requirements registry: {exc}") from exc
    run_id = ""
    if not hasattr(config, "workerinput"):
        from gate.run_id import ensure_run_id

        run_id = ensure_run_id(config)
    plugin = RequirementPlugin(
        registry,
        env_flag(STRICT_ENV),
        out_path=links_path(lane=os.environ.get(LANE_ENV, "").strip() or "default"),
        run_id=run_id,
    )
    config.pluginmanager.register(plugin, PLUGIN_NAME)
    return plugin


def pytest_configure(config: pytest.Config) -> None:
    register(config)
