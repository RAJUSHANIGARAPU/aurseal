"""Keyed masking: deterministic per key, format kept, untouched fields untouched."""

from __future__ import annotations

import io
import json

import pytest

from test_data.synthetic import mask_record
from test_data.synthetic.__main__ import main
from test_data.synthetic.mask import mask_key, mask_value

RECORD = {
    "id": 42,
    "email": "jane.doe@example.org",
    "phone": "+31 20-555 0101",
    "name": "Jane Doe-Smith",
    "account": "XX12BANK0123456789",
    "status": "active",
}
RULES = {"email": "email", "phone": "digits", "name": "letters", "account": "hash", "id": "drop"}


def test_deterministic_per_key():
    assert mask_record(RECORD, RULES, b"k1") == mask_record(RECORD, RULES, b"k1")


def test_different_key_different_output():
    a, b = mask_record(RECORD, RULES, b"k1"), mask_record(RECORD, RULES, b"k2")
    for field in ("email", "phone", "name", "account"):
        assert a[field] != b[field], field


def test_format_is_preserved():
    masked = mask_record(RECORD, RULES, b"k1")
    assert masked["email"].endswith("@example.test") and masked["email"] != RECORD["email"]
    phone = masked["phone"]
    assert len(phone) == len(RECORD["phone"])
    assert [c for c in phone if not c.isdigit()] == [c for c in RECORD["phone"] if not c.isdigit()]
    name = masked["name"]
    assert len(name) == len(RECORD["name"]) and name[4] == " " and "-" in name
    assert name[0].isupper() and name != RECORD["name"]
    assert len(masked["account"]) == 16


def test_unmasked_fields_untouched_and_dropped_fields_gone():
    masked = mask_record(RECORD, RULES, b"k1")
    assert masked["status"] == "active"
    assert "id" not in masked


def test_integers_stay_integers_under_digits():
    assert isinstance(mask_value(123456, "digits", b"k"), int)


def test_same_value_masks_the_same_across_records_so_joins_survive():
    a = mask_record({"email": "x@example.org"}, {"email": "email"}, b"k")
    b = mask_record({"email": "x@example.org", "other": 1}, {"email": "email"}, b"k")
    assert a["email"] == b["email"]


def test_refuses_without_a_key_or_with_an_unknown_rule():
    with pytest.raises(ValueError):
        mask_record(RECORD, RULES, b"")
    with pytest.raises(ValueError):
        mask_record(RECORD, {"email": "scramble"}, b"k")
    with pytest.raises(ValueError):
        mask_key({})
    assert mask_key({"SYNTHETIC_MASK_KEY": "s3"}) == b"s3"


def test_cli_mask(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_MASK_KEY", "k1")
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps(RULES), encoding="utf-8")
    out = io.StringIO()
    stdin = io.StringIO(json.dumps(RECORD) + "\n")
    assert main(["mask", "--rules", str(rules)], stdin=stdin, out=out) == 0
    assert json.loads(out.getvalue()) == mask_record(RECORD, RULES, b"k1")
