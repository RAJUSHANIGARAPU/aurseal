"""Seeded generation: reproducible, valid records are valid, invalid ones break one named thing."""

from __future__ import annotations

import io
import json
import random
import re
import subprocess
import sys
from pathlib import Path

import pytest

from test_data.synthetic import FieldSpec, Profile, SyntheticGenerator, SyntheticParams
from test_data.synthetic.__main__ import main
from test_data.synthetic.generator import MAX_SAFE_INT, STRING_EXTREMES
from test_data.synthetic.regex_sample import UnsupportedPattern, sample

ROOT = Path(__file__).resolve().parents[3]

PROFILE = Profile.from_dict(
    {
        "entity": "order",
        "fields": [
            {"name": "ref", "kind": "string", "required": True, "pattern": r"ORD-\d{3}"},
            {"name": "title", "kind": "string", "required": True, "min_len": 2, "max_len": 40},
            {"name": "qty", "kind": "integer", "required": True, "min": 1, "max": 99},
            {"name": "price", "kind": "number", "min": 0.5, "max": 500},
            {"name": "owner", "kind": "email"},
            {"name": "due", "kind": "date"},
            {"name": "express", "kind": "boolean"},
            {"name": "channel", "kind": "choice", "required": True, "choices": ["web", "phone"]},
            {"name": "note", "kind": "string"},
            {"name": "count", "kind": "integer"},
        ],
    }
)


def _dump(records):
    return [r.to_dict() for r in records]


def test_same_seed_same_output_twice_and_in_another_process():
    first = _dump(SyntheticGenerator(7).generate(PROFILE, 12, "valid"))
    assert _dump(SyntheticGenerator(7).generate(PROFILE, 12, "valid")) == first
    code = (
        "import json, sys; from test_data.synthetic import SyntheticGenerator, Profile; "
        "p = Profile.from_dict(json.load(sys.stdin)); "
        "print(json.dumps([r.to_dict() for r in SyntheticGenerator(7).generate(p, 12, 'valid')]))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        input=json.dumps(PROFILE.to_dict()),
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={"PYTHONHASHSEED": "99", "PATH": ""},
        check=True,
    ).stdout
    assert json.loads(out) == json.loads(json.dumps(first))


def test_a_record_is_reproducible_from_its_coordinates_alone():
    batch = SyntheticGenerator(3).generate(PROFILE, 30, "invalid")
    again = SyntheticGenerator(3).record(PROFILE, "invalid", 17)
    assert again == batch[17]


def test_different_seed_different_output():
    a = SyntheticGenerator(1).generate(PROFILE, 5, "valid")
    b = SyntheticGenerator(2).generate(PROFILE, 5, "valid")
    assert _dump(a) != _dump(b)


def test_valid_records_satisfy_every_constraint():
    for record in SyntheticGenerator(11).generate(PROFILE, 50, "valid"):
        assert PROFILE.check(record.values) == [], record
        assert re.fullmatch(r"ORD-\d{3}", record.values["ref"])


def test_each_invalid_record_breaks_exactly_the_named_constraint():
    generator = SyntheticGenerator(5)
    cases = generator.cases(PROFILE, "invalid")
    records = generator.generate(PROFILE, len(cases), "invalid")
    labels = {r.broken_constraint for r in records}
    for record in records:
        assert PROFILE.check(record.values) == [record.broken_constraint]
        assert record.expected == "reject"
    for expected in (
        "ref.required",
        "ref.pattern",
        "title.min_len",
        "title.max_len",
        "qty.min",
        "qty.max",
        "qty.type",
        "price.min",
        "owner.format",
        "due.format",
        "channel.choices",
        "express.type",
    ):
        assert expected in labels, expected


def test_boundary_hits_min_and_max_and_stays_valid():
    generator = SyntheticGenerator(5)
    records = generator.generate(PROFILE, len(generator.cases(PROFILE, "boundary")), "boundary")
    for record in records:
        assert PROFILE.check(record.values) == []
        assert record.expected == "accept"
    titles = {len(r.values["title"]) for r in records if r.strategy in ("min_len", "max_len")}
    assert {2, 40} <= titles
    qtys = {r.values["qty"] for r in records if r.strategy in ("min", "max")}
    assert {1, 99} <= qtys


def test_named_extremes_land_where_the_profile_puts_them():
    generator = SyntheticGenerator(0)
    boundary = generator.generate(PROFILE, len(generator.cases(PROFILE, "boundary")), "boundary")
    invalid = generator.generate(PROFILE, len(generator.cases(PROFILE, "invalid")), "invalid")
    # `note` has no length limit: every extreme is a valid boundary value.
    notes = {r.strategy for r in boundary if r.values.get("note") in STRING_EXTREMES.values()}
    assert {"rtl_override", "zero_width", "emoji", "rtl_script", "long_256"} <= notes
    # `title` allows at most 40: the 256-char extreme is invalid, labelled max_len.
    assert any(r.strategy == "long_256" and r.broken_constraint == "title.max_len" for r in invalid)
    # `count` has no maximum: 2**53 is a boundary value; `qty` stops at 99: it is invalid.
    assert any(r.values.get("count") == MAX_SAFE_INT for r in boundary)
    assert any(r.strategy == "max_safe_int" and r.broken_constraint == "qty.max" for r in invalid)


def test_works_without_faker(monkeypatch):
    monkeypatch.setitem(sys.modules, "faker", None)
    generator = SyntheticGenerator(4)
    assert generator.faker_available is False
    for record in generator.generate(PROFILE, 10, "valid"):
        assert PROFILE.check(record.values) == []


def test_bad_inputs():
    with pytest.raises(ValueError):
        SyntheticGenerator(0).generate(PROFILE, 1, "weird")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        FieldSpec("x", kind="choice")
    with pytest.raises(ValueError):
        FieldSpec("x", kind="blob")


@pytest.mark.parametrize(
    "pattern",
    [r"ORD-\d{3}", r"[A-Z]{2}[0-9]{2}[A-Z0-9]{4,8}", r"(ab|cd)+x?", r"^\w+@\w+\.test$", r"a.c"],
)
def test_regex_sampler_matches(pattern):
    rng = random.Random(1)
    for _ in range(20):
        assert re.fullmatch(pattern, sample(pattern, rng))


@pytest.mark.parametrize("pattern", [r"[^a]", r"(?=x)", r"\bword"])
def test_regex_sampler_refuses_what_it_cannot_honour(pattern):
    with pytest.raises(UnsupportedPattern):
        sample(pattern, random.Random(0))


def test_synthetic_params_carry_values_and_coordinates():
    params = SyntheticParams(PROFILE, "boundary", 7, 3).as_params()
    assert params["SYNTHETIC"] == "order:boundary:7:3"
    assert params["ref"] == SyntheticGenerator(7).record(PROFILE, "boundary", 3).values["ref"]


def test_cli_generate(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(json.dumps(PROFILE.to_dict()), encoding="utf-8")
    out = io.StringIO()
    assert main(["generate", "--profile", str(path), "--mode", "invalid", "--n", "4"], out=out) == 0
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert len(lines) == 4 and all(line["broken_constraint"] for line in lines)


def test_repository_profile_loads():
    profile = Profile.from_json(ROOT / "test_data" / "profiles" / "customer.json")
    assert profile.endpoint and profile.get_field("display_name").required
