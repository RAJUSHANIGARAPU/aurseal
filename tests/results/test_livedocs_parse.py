"""Front matter and directive parsing; malformed input is an issue, never an exception."""

from __future__ import annotations

import pytest

from results.livedocs import parse_page
from tests.results._livedocs_fixture import PAGE

pytestmark = pytest.mark.feature("living-documentation")


def test_front_matter_and_directives(tmp_path):
    p = tmp_path / "billing.md"
    p.write_text(PAGE)
    page = parse_page(p)
    assert page.front == {"feature": "billing", "requirements": ["REQ-12"]}
    assert page.title == "Billing" and page.is_living and not page.issues
    assert [(d.kind, d.args) for d in page.directives] == [
        ("status", {}),
        ("cases", {"feature": "billing"}),
        ("cases", {"requirement": "REQ-12"}),
    ]
    assert page.directives[0].line == 9  # counted in the file, front matter included
    assert page.features == ["billing"] and page.requirements == ["REQ-12"]


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("---\nfeature: [unclosed\n---\n# X\n", "malformed-front-matter"),
        ("---\nfeature: x\n# never closed\n", "malformed-front-matter"),
        ("---\n- a list\n---\n# X\n", "malformed-front-matter"),
        ("# X\n<!-- living:mystery -->\n", "unknown-directive"),
        ("# X\n<!-- living:cases -->\n", "unknown-directive"),
    ],
)
def test_malformed_input_becomes_an_issue(tmp_path, text, code):
    p = tmp_path / "x.md"
    p.write_text(text)
    page = parse_page(p)
    assert [i.code for i in page.issues] == [code]


def test_a_plain_page_is_not_living(tmp_path):
    p = tmp_path / "plain.md"
    p.write_text("# Plain\n\nText.\n")
    page = parse_page(p)
    assert not page.is_living and page.front == {} and not page.issues


def test_directives_inside_code_fences_are_examples_not_directives(tmp_path):
    p = tmp_path / "x.md"
    p.write_text(
        "# X\n\n```markdown\n<!-- living:cases feature=ghost -->\n```\n<!-- living:status -->\n"
    )
    page = parse_page(p)
    assert [d.kind for d in page.directives] == ["status"] and page.directives[0].line == 6
