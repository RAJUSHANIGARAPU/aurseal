"""The demo's document templates against their manifests: coverage, structure and rendering.

``templates/*.manifest.json`` describe every template the demo deploys: its samples, the
text that must appear (``pinned_text``), the labels that must carry a value (``must_show``),
the money lines (``money_fields``), the lines that only print when their value is there
(``conditional_lines``) and where the template is not deployed (``absent_from_envs``).

Per template this checks that the manifest covers it, that its source is well formed and
reads only declared payload paths, that the complete sample renders clean and shows every
conditional line, and that the minimal sample shows none of them and leaves no label
dangling. The one open issue, DEMO-117, is planted with a fault switch: with the switch on
it must reproduce, with it off the same judgement must report it cleared.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from oracles import document_findings, pdf_watermark, placeholder_scan, template_structure
from oracles.pdf_text import extract_text
from verification.known_defects_manifest import (
    by_template,
    check_manifest,
    judge_findings,
    load_manifest,
)

TEMPLATES = Path(__file__).with_name("templates")
ENTRIES = load_manifest(TEMPLATES)
SCHEMA = json.loads((TEMPLATES / "payload.schema.json").read_text(encoding="utf-8"))
KNOWN_ENVS = {"demo", "legacy"}
ENV = os.environ.get("DEMO_TEMPLATE_ENV", "demo")
LOCALE = "en_GB"  # the demo prints EUR 1234.56
MARKER = "EUR"
CONDITIONAL = re.compile(r"^\?\{([\w.]+)\}", re.M)

PLANTED = "account_opened"
PLANTED_ISSUE = "DEMO-117"
PLANTED_FAULT = "doc_amount_unformatted"


def deployed_here(entry):
    marks = []
    if ENV in entry.absent_from_envs:
        marks.append(pytest.mark.skip(reason=f"{entry.template} is not deployed on {ENV}"))
    return pytest.param(entry, id=entry.template, marks=marks)


PARAMS = [deployed_here(e) for e in ENTRIES]


def sample(entry, kind: str) -> dict:
    (path,) = [s for s in entry.samples if s.endswith(f".{kind}.json")]
    return json.loads((TEMPLATES / path).read_text(encoding="utf-8"))


def render_text(documents, entry, payload, **kwargs) -> str:
    return extract_text(documents.render(entry.template, payload, **kwargs))


def findings(text, entry, *, pinned=True):
    return document_findings.all_findings(
        text,
        pinned_text=entry.data["pinned_text"] if pinned else (),
        must_show=entry.data["must_show"],
        section_headings=entry.data["section_headings"],
        money_fields=entry.data["money_fields"],
        locale=LOCALE,
        currency_marker=MARKER,
    )


def read_paths(source: str) -> set[str]:
    return set(placeholder_scan.placeholder_paths(source)) | set(CONDITIONAL.findall(source))


def schema_leaves(node, prefix="") -> set[str]:
    props = node.get("properties")
    if props is None:
        return {prefix}
    return {leaf for k, v in props.items() for leaf in schema_leaves(v, f"{prefix}{k}.")}


def test_every_deployed_template_has_a_sound_entry(documents, demo_control):
    deployed = documents.template_ids()
    coverage = template_structure.manifest_coverage(deployed, [e.template for e in ENTRIES])
    assert coverage.ok, coverage
    assert len(deployed) == 6
    assert (
        check_manifest(ENTRIES, emitted_kinds=document_findings.KINDS, known_envs=KNOWN_ENVS) == []
    )


def test_the_schema_declares_exactly_the_paths_the_templates_read(documents, demo_control):
    read = set().union(
        *(read_paths(documents.template_source(e.template).decode()) for e in ENTRIES)
    )
    declared = {p.rstrip(".") for p in schema_leaves(SCHEMA)}
    assert read == declared, {"undeclared": read - declared, "unread": declared - read}


@pytest.mark.parametrize("entry", PARAMS)
def test_template_structure_is_clean(entry, documents, demo_control):
    source = documents.template_source(entry.template).decode()
    assert template_structure.validate_template(entry.template, source, schema=SCHEMA) == []
    undeclared = [
        p for p in CONDITIONAL.findall(source) if not placeholder_scan.schema_declares(SCHEMA, p)
    ]
    assert undeclared == [], f"conditional lines read undeclared paths {undeclared}"


@pytest.mark.parametrize("entry", PARAMS)
def test_complete_sample_renders_clean(entry, documents, demo_control):
    demo_control.faults()
    pdf = documents.render(entry.template, sample(entry, "complete"))
    pdf_watermark.assert_unwatermarked(pdf)
    text = extract_text(pdf)
    found = findings(text, entry)
    assert found == [], document_findings.describe(entry.template, found)
    missing = [label for label in entry.data["conditional_lines"] if label not in text]
    assert missing == [], f"the complete sample should show {missing}"


@pytest.mark.parametrize("entry", PARAMS)
def test_minimal_sample_leaves_no_dangling_label(entry, documents, demo_control):
    demo_control.faults()
    text = render_text(documents, entry, sample(entry, "minimal"))
    found = findings(text, entry, pinned=False)
    assert found == [], document_findings.describe(entry.template, found)
    shown = [label for label in entry.data["conditional_lines"] if label in text]
    assert shown == [], f"the minimal sample should show none of {shown}"


@pytest.mark.parametrize("fault_on", [True, False], ids=["fault-on", "fault-off"])
def test_planted_issue_reproduces_only_with_its_fault(fault_on, documents, demo_control):
    entry = by_template(ENTRIES, PLANTED)
    assert entry is not None and [d.issue for d in entry.open_issues] == [PLANTED_ISSUE]
    demo_control.faults(*([PLANTED_FAULT] if fault_on else []))
    text = render_text(documents, entry, sample(entry, "complete"))
    assert "SUB-004512" in text and "Charge per period:" in text, "control: the letter rendered"

    found = findings(text, entry)
    judgement = judge_findings(entry, [f.kind for f in found], document_findings.KINDS)
    (verdict,) = judgement.verdicts
    if fault_on:
        assert verdict.verdict.value == "reproduces"
        # The pinned charge line is the same defect seen a second time, and nothing else is.
        assert [(f.kind, f.detail) for f in found if f.kind != "RAW-AMOUNT"] == [
            ("ABSENT-VALUE", "pinned text 'Charge per period: EUR 117.00' does not appear anywhere")
        ]
    else:
        assert found == [], document_findings.describe(entry.template, found)
        assert verdict.verdict.value == "cleared"
        with pytest.raises(AssertionError, match=f"KNOWN DEFECT CLEARED: {PLANTED_ISSUE}"):
            judgement.assert_clean()


def test_overlay_banner_check_still_fires(documents, demo_control):
    """Positive control for the overlay check every render above passes."""
    (entry,) = [e for e in ENTRIES if e.template == "invoice"]
    stamped = documents.render("invoice", sample(entry, "complete"), watermark="Specimen")
    with pytest.raises(pdf_watermark.OverlayFound, match="specimen"):
        pdf_watermark.assert_unwatermarked(stamped)
