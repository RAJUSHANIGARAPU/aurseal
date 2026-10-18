"""Tests for document_findings, placeholder_scan and template_structure."""

from __future__ import annotations

import io
import zipfile

import pytest

from oracles import document_findings as di
from oracles import placeholder_scan as ps
from oracles import template_structure as ts

GOOD = """INVOICE
Invoice number      INV-2030-0007
Customer            Example Trading Ltd
Delivery address
    12 Sample Street
    1000 AA Exampletown
Total due           € 1.234,56
"""

BROKEN = """INVOICE
Invoice number
Customer            null
Reference <[order.reference]>>
Total due           1234.56
Delivery address

"""


class TestDocumentIntegrity:
    def test_clean_document_has_no_findings(self):
        found = di.all_findings(
            GOOD,
            pinned_text={"customer": "Example Trading Ltd"},
            must_show=["Invoice number", "Total due"],
            section_headings=["Delivery address"],
            money_fields=["Total due"],
        )
        assert found == []

    def test_every_oracle_bites(self):
        found = di.all_findings(
            BROKEN,
            pinned_text={"customer": "Example Trading Ltd"},
            must_show=["Invoice number"],
            section_headings=["Delivery address"],
            money_fields=["Total due"],
        )
        assert {f.kind for f in found} == di.KINDS

    def test_literal_nulls_are_whole_words_only(self):
        assert di.null_texts("Nullable fields annulled; NaNo") == []
        assert [f.detail for f in di.null_texts("x: undefined, y: [object Object]")] == [
            "the word 'undefined' stands where a value belongs",
            "the word '[object Object]' stands where a value belongs",
        ]

    def test_block_label_does_not_rescue_a_row_label(self):
        text = "Purpose :\nSome unrelated line\n"
        assert di.dangling_headings(text, ["Purpose"]) == []
        assert [f.kind for f in di.dangling_labels(text, ["Purpose"])] == ["DANGLING-LABEL"]

    def test_unformatted_amounts_are_locale_aware(self):
        assert di.raw_amounts("Total 1,234.56", ["Total"], "en_US") == []
        assert di.raw_amounts("Total 1234,56", ["Total"], "en_US")[0].kind == ("RAW-AMOUNT")

    def test_none_text_is_no_findings_not_a_crash(self):
        assert di.all_findings(None, must_show=["x"]) == []


def build_docx(parts: dict[str, str]) -> bytes:
    """A minimal ZIP package holding the given parts, enough for the source reader."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("[Content_Types].xml", "<Types/>")
        for name, xml in parts.items():
            package.writestr(name, xml)
    return buffer.getvalue()


# A placeholder typed once but saved over three runs, braces entity-encoded.
BODY_OVER_THREE_RUNS = (
    "<w:document><w:body><w:p>"
    "<w:r><w:t>Balance: &#123;&#123; </w:t></w:r>"
    "<w:r><w:t>ledger.bal</w:t></w:r>"
    "<w:r><w:t>ance &#125;&#125;</w:t></w:r>"
    "</w:p></w:body></w:document>"
)

LEGACY_BYTES = bytes.fromhex("D0CF11E0A1B11AE1") + bytes(24)


class TestReadingPackages:
    def test_body_and_footer_are_read_but_styles_are_not(self):
        data = build_docx(
            {
                "word/document.xml": BODY_OVER_THREE_RUNS,
                "word/footer2.xml": "<w:ftr><w:p><w:t>{{ branch.phone }}</w:t></w:p></w:ftr>",
                "word/styles.xml": "<w:styles><w:t>{{ theme.colour }}</w:t></w:styles>",
            }
        )
        source = ps.package_source_text(data)
        assert ps.placeholder_paths(source) == ["ledger.balance", "branch.phone"]

    def test_legacy_doc_is_reported_not_scanned(self):
        assert ps.is_compound_file(LEGACY_BYTES)
        assert not ps.is_zip_package(LEGACY_BYTES)
        with pytest.raises(ValueError, match="unreadable"):
            ps.package_source_text(LEGACY_BYTES)
        (finding,) = ts.validate_template("archive.doc", LEGACY_BYTES)
        assert finding.kind == "UNREADABLE" and "legacy" in finding.detail

    @pytest.mark.parametrize("data", [b"", b"just some words", b"PK"])
    def test_non_packages_are_not_zip_packages(self, data):
        assert not ps.is_zip_package(data)


class TestCurlyDialect:
    def test_only_value_placeholders_are_paths(self):
        text = (
            "{{ ledger.owner }} {{fee|money}} {{#if overdue}}late{{else}}fine{{/if}} "
            "{{!internal note}} {{>footer}} {{^empty}}{{/empty}}"
        )
        assert ps.placeholder_paths(text) == ["ledger.owner", "fee"]

    def test_repeats_are_kept_in_document_order(self):
        assert ps.placeholder_paths("{{b}} {{a}} {{b}}") == ["b", "a", "b"]

    @pytest.mark.parametrize(
        "text",
        [
            "Owner: { ledger.owner }}",
            "Owner: {{ ledger.owner }",
            "Owner: {{ ledger.owner",
            "Fee: {fee|money}}",
        ],
    )
    def test_damaged_brackets_are_reported(self, text):
        assert ps.damaged_placeholders(text)

    @pytest.mark.parametrize(
        "text",
        [
            "Owner: {{ ledger.owner }} {{#if overdue}}late{{/if}} {{ fee|money }}",
            "?{branch.phone}Phone: {{branch.phone}}",
            "{{#each rows}}{{cell}}{{/each}}",
        ],
    )
    def test_well_formed_text_has_no_damage(self, text):
        assert ps.damaged_placeholders(text) == []

    def test_an_else_belongs_to_its_own_block(self):
        text = (
            "{{#if member}} {{#if senior}}A{{else}}B{{/if}} {{/if}} "
            "{{#if guest}}C{{else}}D{{/if}} "
            "{{#if vip}}{{#each perks}}x{{else}}none{{/each}}{{/if}}"
        )
        assert ps.conditional_blocks(text) == [
            ps.Conditional("member", False),
            ps.Conditional("senior", True),
            ps.Conditional("guest", True),
            ps.Conditional("vip", False),
        ]

    def test_an_unclosed_block_runs_to_the_end(self):
        assert ps.conditional_blocks("{{#if open}}x{{else}}y") == [ps.Conditional("open", True)]

    def test_filter_quotes_are_classified(self):
        text = '{{due|date:"D/M/Y"}} {{since|date:“Y”}} {{until|date:"M”}} {{fee|money}}'
        found = ps.value_filters(text)
        assert [f.text for f in found] == ["D/M/Y", "Y", "M", "money"]
        assert [f.uses_typographic_quote for f in found] == [False, True, True, False]
        assert [f.quotes_disagree for f in found] == [False, False, True, False]
        assert (found[3].left_quote, found[3].right_quote) == ("", "")

    def test_top_level_names_cover_loops_and_conditions(self):
        text = "{{#each rows}}{{cell}}{{/each}} {{ ledger.owner }} {{#if this}}{{/if}}"
        assert set(ps.top_level_names(text)) == {"rows", "cell", "ledger"}

    def test_top_level_names_skip_literal_words(self):
        assert ps.top_level_names("{{#if true}}{{/if}}{{#if false}}{{/if}}{{this}}") == []


class TestWordFieldDialect:
    def test_names_with_and_without_quotes_and_switches(self):
        text = ' MERGEFIELD payer_name \\* MERGEFORMAT  MERGEFIELD "amount_due" '
        assert ps.placeholder_paths(text, "mergefield") == ["payer_name", "amount_due"]

    def test_a_field_without_a_name_is_damaged(self):
        assert ps.damaged_placeholders(" MERGEFIELD \\* MERGEFORMAT", "mergefield")
        assert ps.damaged_placeholders(" MERGEFIELD payer_name ", "mergefield") == []

    def test_conditions_have_no_else_and_there_are_no_filters(self):
        text = '{ IF { MERGEFIELD vip } = "Y" "Gold" "" }'
        assert ps.conditional_blocks(text, "mergefield") == [ps.Conditional("vip", False)]
        assert ps.value_filters(text, "mergefield") == []

    def test_unknown_dialect_lists_the_known_ones(self):
        for scan in (
            ps.placeholder_paths,
            ps.damaged_placeholders,
            ps.conditional_blocks,
            ps.value_filters,
            ps.top_level_names,
        ):
            with pytest.raises(ValueError, match="mergefield.*mustache"):
                scan("{{x}}", "angle")
        with pytest.raises(ValueError, match="mergefield.*mustache"):
            ps.undeclared_paths("{{x}}", {}, "angle")


SCHEMA = {
    "type": "object",
    "properties": {
        "ledger": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "balance": {"type": "number"},
                "rows": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"cell": {"type": "string"}}},
                },
                "tags": {"type": "object", "additionalProperties": True},
                "closed": {"type": "object"},
            },
        },
    },
}


class TestSchemaChecks:
    @pytest.mark.parametrize(
        "path,declared",
        [
            ("ledger.owner", True),
            ("ledger.rows.cell", True),
            ("ledger.tags.anything", True),
            ("ledger.ownr", False),
            ("ledger.closed.anything", False),
            ("nothing", False),
        ],
    )
    def test_schema_declares(self, path, declared):
        assert ps.schema_declares(SCHEMA, path) is declared

    def test_nested_arrays_are_stepped_through(self):
        grid = {
            "properties": {
                "grid": {
                    "type": "array",
                    "items": {"type": "array", "items": {"properties": {"v": {}}}},
                }
            }
        }
        assert ps.schema_declares(grid, "grid.v")
        assert ps.schema_declares({"additionalProperties": {"type": "string"}}, "free.form")

    def test_undeclared_paths_are_reported_once(self):
        text = "{{ ledger.owner }} {{ ledger.ownr }} {{ ledger.rows.cell }} {{ ledger.ownr }}"
        assert ps.undeclared_paths(text, SCHEMA) == ["ledger.ownr"]

    def test_loop_aliases_resolve_but_report_as_written(self):
        text = "{{ r.cell }} {{ r.colour }}"
        aliases = {"r": "ledger.rows"}
        assert ps.undeclared_paths(text, SCHEMA, loop_aliases=aliases) == ["r.colour"]
        assert ps.undeclared_paths(text, SCHEMA, loop_aliases=aliases, ignore=["r.colour"]) == []
        assert (
            ps.undeclared_paths(text, SCHEMA, loop_aliases=aliases, ignore=["ledger.rows.colour"])
            == []
        )

    def test_validate_template_combines_the_checks(self):
        text = (
            "{{ ledger.owner }} {{ ledger.ownr }} { ledger.balance }} "
            "{{ ledger.balance|number:“0.00” }}"
        )
        found = ts.validate_template(
            "statement", text, syntax="mustache", schema=SCHEMA, known_formats=[]
        )
        assert {f.kind for f in found} == {
            "BAD-DELIMITER",
            "CURLY-QUOTE",
            "UNDECLARED-PATH",
            "UNKNOWN-FILTER",
        }


class TestManifestAndRendering:
    def test_manifest_coverage(self):
        cov = ts.manifest_coverage(
            ["a", "b", "c", "c", "ledger-x", "q1"],
            ["a", "b"],
            quarantine=["q1", "gone", "a"],
            exempt_prefixes=["ledger-"],
        )
        assert cov.undescribed == ["c"] and cov.duplicates == ["c"]
        assert cov.stale_quarantine == ["a", "gone"] and not cov.ok
        assert ts.manifest_coverage(["a"], ["a"]).ok

    def test_check_rendered_runs_every_oracle_per_entry(self):
        entries = [
            ts.RenderEntry("good", {}, {"customer": "Example Trading Ltd"}, ("Invoice number",)),
            ts.RenderEntry("bad", {}, {}, ("Invoice number",)),
        ]
        docs = {"good": GOOD, "bad": BROKEN}
        result = ts.check_rendered(entries, lambda name, payload: docs[name])
        assert result["good"] == [] and result["bad"]
        with pytest.raises(ValueError, match="extract"):
            ts.check_rendered(entries, lambda n, p: b"%PDF")
        as_bytes = ts.check_rendered(entries[:1], lambda n, p: GOOD.encode(), bytes.decode)
        assert as_bytes == {"good": []}


class TestMoneyLines:
    def test_a_currency_marker_catches_a_bare_amount_in_a_shared_locale(self):
        text = "Charge per period: 117.0\nNet: EUR 97.50"
        assert di.raw_amounts(text, ["Net:"], "en_GB", currency_marker="EUR") == []
        found = di.raw_amounts(text, ["Charge per period:"], "en_GB", currency_marker="EUR")
        assert [f.kind for f in found] == ["RAW-AMOUNT"] and "117.0" in found[0].detail
        assert di.raw_amounts(text, ["Charge per period:"], "en_GB") == []

    def test_pinned_text_can_be_a_plain_list(self):
        found = di.absent_values("Invoice 7\nTotal: EUR 3.00", ["Invoice 7", "Total: EUR 4.00"])
        assert [f.detail for f in found] == [
            "pinned text 'Total: EUR 4.00' does not appear anywhere"
        ]
