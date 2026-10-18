"""Document content on seeded subscriptions, judged per environment, for both plan kinds.

Seeds a subscription with the content fixture but no billing contact, then reads the letter
issued on activation (account opened) and the notice issued by a change (plan update, a
different stored file).

The two tracked oracles - no contact line without a contact, and a charge printed as money -
go through :func:`verification.fix_scope.judge`: where the fix is expected
(``DEMO_DOCUMENT_FIX_ENVS``, default ``healthy``) they are asserted and recorded as proved;
elsewhere they run as expected-red detectors with the detector examples' signatures. The demo
plays both environments: ``healthy`` (no fault) and ``faulty`` (both document faults on). The
data region and workspace are always asserted. All findings on a document are reported
together.
"""

from __future__ import annotations

import pytest

from tests.demo_examples.documents import oracles
from tests.demo_examples.documents._support import (
    CONTENT_TEMPLATES,
    HEADINGS,
    content_fields,
    document_texts,
    seed_with_documents,
)
from verification.evidence_verdict import EvidenceVerdict
from verification.fix_scope import fix_scope_envs, judge
from verification.verdicts import RedVerdict

FIX_ENVS_VARIABLE = "DEMO_DOCUMENT_FIX_ENVS"
FAULTS = {"healthy": (), "faulty": ("doc_amount_unformatted", "doc_optional_prints_none")}


@pytest.mark.parametrize("env", sorted(FAULTS))
@pytest.mark.parametrize("plan_kind", ["INDIVIDUAL", "TEAM"])
def test_new_and_change_document_content(plan_kind, env, demo_control, demo_seed, documents):
    assert demo_control.faults(*FAULTS[env]) == sorted(FAULTS[env])
    fields = content_fields(with_contact=False)
    sub = seed_with_documents(demo_control, demo_seed, plan_kind=plan_kind, **fields)
    issued = {
        d["template"]: d
        for d in documents.documents_of(sub["id"])
        if d["template"] in CONTENT_TEMPLATES.values()
    }
    new, change = (issued[CONTENT_TEMPLATES[k]] for k in ("new", "change"))
    assert new["file_id"] != change["file_id"], "the change reused the new letter's file"
    texts = document_texts(documents, sub["id"], tuple(CONTENT_TEMPLATES.values()))

    fixed_on = fix_scope_envs(FIX_ENVS_VARIABLE, default="healthy")
    verdicts: dict[str, RedVerdict] = {}
    opened = texts["account_opened"]

    def control() -> None:
        oracles.assert_document_exercised(opened, sub["number"], "Data region:")
        first = oracles.non_blank_lines(opened)[0]
        assert first.startswith(HEADINGS["account_opened"]), f"unexpected heading {first!r}"

    def tracked(name, ticket, signature, oracle):
        def run() -> None:
            verdicts[name] = judge(
                env,
                fixed_on,
                ticket,
                signature,
                coverage=EvidenceVerdict(f"account_opened {name} ({plan_kind})", ticket=ticket),
                control=control,
                oracle=oracle,
                evidence=lambda: f"{name} on the account-opened letter of {sub['number']}",
            )

        return run

    def values_shown(text) -> None:
        oracles.assert_line_value(text, "Data region:", fields["data_region"])
        oracles.assert_line_value(text, "Workspace:", fields["workspace_type"])

    oracles.assert_all(
        {
            "contact line": tracked(
                "contact line",
                oracles.CONTACT_DEFECT,
                oracles.CONTACT_SIGNATURE,
                lambda: oracles.assert_contact_line_absent(opened),
            ),
            "charge format": tracked(
                "charge format",
                oracles.AMOUNT_DEFECT,
                oracles.AMOUNT_SIGNATURE,
                lambda: oracles.assert_period_charge_formatted(opened),
            ),
            "values on the letter": lambda: values_shown(opened),
            "values on the notice": lambda: values_shown(texts["plan_update_notice"]),
        }
    )
    expected = RedVerdict.CLEARED if env in fixed_on else RedVerdict.REPRODUCES
    assert set(verdicts.values()) == {expected} and len(verdicts) == 2, verdicts
    EvidenceVerdict(f"term_extension_notice content ({plan_kind})").held_empty(
        "the term extension notice has no details section, so neither line is checked there"
    )
