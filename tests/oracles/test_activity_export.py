"""Tests for oracles.activity_export: zip + RFC 4180 CSV + map-cell tokenizer."""

from __future__ import annotations

import io
import zipfile

import pytest

from oracles.activity_export import (
    CallRecord,
    ColumnMap,
    ExportFormatError,
    parse_map_cell,
    read_activity_export,
    read_rows,
)


def _zip(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in entries.items():
            zf.writestr(name, text)
    return buf.getvalue()


PLAIN_CSV = (
    "actor,method,path,status,note\r\n"
    'ops@example.test,get,/api/orders/ORD-42,200,"multi\r\nline, quoted ""note"""\r\n'
    "svc-batch@example.test,POST,/api/orders,201,\r\n"
)


class TestParseMapCell:
    def test_simple_map(self):
        assert parse_map_cell("{a=1, b=two}") == {"a": "1", "b": "two"}

    def test_values_with_commas_are_kept_whole(self):
        cell = "{method=GET, path=/api/orders?ids=1,2,3&sort=a, status=200}"
        assert parse_map_cell(cell) == {
            "method": "GET",
            "path": "/api/orders?ids=1,2,3&sort=a",
            "status": "200",
        }

    def test_nested_and_quoted_values(self):
        cell = '{user={id=7, mail=a@example.test}, tags=[x, y=z], note="a, b=c"}'
        assert parse_map_cell(cell) == {
            "user": "{id=7, mail=a@example.test}",
            "tags": "[x, y=z]",
            "note": "a, b=c",
        }

    def test_braces_optional_and_empty(self):
        assert parse_map_cell("k=v") == {"k": "v"}
        assert parse_map_cell("{}") == {}

    @pytest.mark.parametrize("bad", ["{a={b=1}", "{a=1]}", "{novalue}"])
    def test_malformed_cells_raise(self, bad):
        with pytest.raises(ExportFormatError):
            parse_map_cell(bad)


class TestReadActivityExport:
    def test_plain_columns_with_rfc4180_quoting(self):
        rows = read_activity_export(_zip({"call_log.csv": PLAIN_CSV}))
        assert rows == [
            CallRecord("ops@example.test", "GET", "/api/orders/ORD-42", 200),
            CallRecord("svc-batch@example.test", "POST", "/api/orders", 201),
        ]
        assert rows[0].row["note"] == 'multi\r\nline, quoted "note"'

    def test_map_columns(self):
        csv_text = (
            "who,request,result\n"
            '"{name=Ops, mail=ops@example.test}",'
            '"{verb=delete, url=/api/items?ids=4,5}","{code=204}"\n'
        )
        columns = ColumnMap(
            actor=("who", "mail"),
            method=("request", "verb"),
            path=("request", "url"),
            status=("result", "code"),
        )
        data = _zip({"export/Activity.CSV": csv_text})
        [row] = read_activity_export(data, entry="activity.csv", columns=columns)
        assert row == CallRecord("ops@example.test", "DELETE", "/api/items?ids=4,5", 204)

    def test_missing_values_become_none(self):
        data = _zip({"call_log.csv": "actor,method,path,status\n,,,\n"})
        assert read_activity_export(data) == [CallRecord(None, None, None, None)]

    def test_entry_found_by_name_ignoring_case_and_directory(self):
        data = _zip({"run-7/CALL_LOG.csv": PLAIN_CSV, "other.csv": "x\n1\n"})
        assert len(read_rows(data)) == 2

    def test_missing_entry_lists_the_archive(self):
        with pytest.raises(ExportFormatError, match=r"entries: \['other.csv'\]"):
            read_rows(_zip({"other.csv": "x\n"}))

    def test_not_a_zip(self):
        with pytest.raises(ExportFormatError, match="not a zip"):
            read_rows(b"<html>")

    def test_non_numeric_status(self):
        data = _zip({"call_log.csv": "actor,method,path,status\na,GET,/x,ok\n"})
        with pytest.raises(ExportFormatError, match="status 'ok'"):
            read_activity_export(data)

    def test_utf8_bom_is_ignored(self):
        data = _zip({"call_log.csv": "﻿" + PLAIN_CSV})
        assert read_activity_export(data)[0].actor == "ops@example.test"
