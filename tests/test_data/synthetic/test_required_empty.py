"""Required-field probes with explicit empty strings: the generator's required-empty variants."""

from __future__ import annotations

from test_data.synthetic.generator import REQUIRED_EMPTY, SyntheticGenerator
from test_data.synthetic.profile import FieldSpec, Profile

PROFILE = Profile(
    entity="task",
    fields=(
        FieldSpec(name="title", kind="string", required=True, min_len=1, max_len=80),
        FieldSpec(name="note", kind="string", required=False, max_len=200),
        FieldSpec(name="contact", kind="email", required=True),
        FieldSpec(name="seats", kind="integer", required=True, min=1, max=500),
    ),
)


def _variants(profile: Profile) -> dict[tuple[str, str], object]:
    records = SyntheticGenerator(seed=7).generate(profile, 40, "invalid")
    return {
        (r.broken_constraint, r.strategy): r.values.get(r.broken_constraint.split(".")[0])
        for r in records
        if r.strategy.startswith(REQUIRED_EMPTY)
    }


def test_every_required_string_field_gets_an_empty_and_a_blank_variant():
    found = _variants(PROFILE)
    assert found == {
        ("title.required", "required-empty"): "",
        ("title.required", "required-empty-whitespace"): "   ",
        ("contact.required", "required-empty"): "",
        ("contact.required", "required-empty-whitespace"): "   ",
    }


def test_optional_and_non_string_fields_get_none():
    keys = {field for field, _ in _variants(PROFILE)}
    assert "note.required" not in keys and "seats.required" not in keys


def test_blank_required_values_are_refused_by_the_profile_itself():
    base = {"title": "Plan review", "contact": "a1@example.test", "seats": 3}
    assert PROFILE.check(base) == []
    assert PROFILE.check({**base, "title": ""}) == ["title.required"]
    assert PROFILE.check({**base, "title": " \t"}) == ["title.required"]
    assert PROFILE.check({**base, "note": ""}) == [], "blank is fine where nothing is required"


def test_the_absent_field_variant_is_still_there():
    records = SyntheticGenerator(seed=7).generate(PROFILE, 40, "invalid")
    assert any(r.strategy == "missing" and "title" not in r.values for r in records)


def test_short_max_len_keeps_the_blank_variant_within_length():
    profile = Profile("p", (FieldSpec(name="code", kind="string", required=True, max_len=2),))
    assert _variants(profile)[("code.required", "required-empty-whitespace")] == "  "
