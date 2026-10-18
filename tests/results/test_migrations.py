"""Forward-only migrations: apply, re-apply, checksums, domain order, requires, prefixes."""

from __future__ import annotations

import sqlite3

import pytest

from results import migrations
from results.migrations import MigrationError


def _conn(path=":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _tables(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_fresh_apply_creates_the_core_schema():
    conn = _conn()
    applied = migrations.apply(conn)
    assert [(m.domain, m.version) for m in applied][0] == ("core", 1)
    expected = {
        "projects",
        "runs",
        "invocations",
        "cases",
        "case_revisions",
        "test_results",
        "evidence",
        "verdicts",
        "data_origin",
        "case_links",
        "schema_migrations",
    }
    assert expected <= _tables(conn)
    assert conn.execute("SELECT id FROM projects").fetchall() == [("default",)]


def test_every_data_table_carries_project_id():
    conn = _conn()
    migrations.apply(conn)
    meta = {"schema_migrations", "projects"}
    for table in _tables(conn) - meta:
        if table.startswith(("sqlite_", "search_docs_")):
            continue
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        assert "project_id" in cols, table


def test_reapply_is_a_no_op():
    conn = _conn()
    migrations.apply(conn)
    assert migrations.apply(conn) == []


def test_user_version_mirrors_the_core_version():
    conn = _conn()
    applied = migrations.apply(conn)
    core = max(m.version for m in applied if m.domain == "core")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == core


def _domain(tmp_path, name, files: dict[str, str], prefix=None):
    d = tmp_path / f"dom-{name}"
    d.mkdir()
    for fname, sql in files.items():
        (d / fname).write_text(sql)
    migrations.register_domain(name, d, prefix)
    return d


def test_a_tampered_applied_migration_is_refused(tmp_path):
    d = _domain(tmp_path, "zz", {"0001_init.sql": "CREATE TABLE zz_things (id INTEGER);\n"})
    conn = _conn(tmp_path / "db.sqlite")
    migrations.apply(conn)
    (d / "0001_init.sql").write_text("CREATE TABLE zz_things (id INTEGER, extra TEXT);\n")
    with pytest.raises(MigrationError, match="checksum"):
        migrations.apply(conn)
    states = {(s.domain, s.version): s for s in migrations.status(conn)}
    assert states[("zz", 1)].checksum_ok is False


def test_domains_apply_after_core_alphabetically_and_honour_requires(tmp_path):
    _domain(
        tmp_path,
        "bb",
        {"0001_init.sql": "-- requires: aa>=1, core>=1\nCREATE TABLE bb_x (id INTEGER);\n"},
    )
    _domain(tmp_path, "aa", {"0001_init.sql": "CREATE TABLE aa_x (id INTEGER);\n"})
    conn = _conn()
    order = [m.domain for m in migrations.apply(conn)]
    assert order.index("core") < order.index("aa") < order.index("bb")


def test_an_unmet_requirement_stops_before_running(tmp_path):
    _domain(tmp_path, "ab", {"0001_init.sql": "-- requires: zz>=1\nCREATE TABLE ab_x (id INT);\n"})
    _domain(tmp_path, "zz", {"0001_init.sql": "CREATE TABLE zz_x (id INT);\n"})
    conn = _conn()
    with pytest.raises(MigrationError, match="requires zz>=1"):
        migrations.apply(conn)
    assert "ab_x" not in _tables(conn)


def test_a_domain_may_only_create_its_own_prefix(tmp_path):
    _domain(tmp_path, "pp", {"0001_init.sql": "CREATE TABLE runs_shadow (id INT);\n"})
    with pytest.raises(MigrationError, match="pp_"):
        migrations.apply(_conn())


def test_a_domain_may_not_alter_core_tables(tmp_path):
    _domain(tmp_path, "pp", {"0001_init.sql": "ALTER TABLE runs ADD COLUMN pp_x TEXT;\n"})
    with pytest.raises(MigrationError, match="altered"):
        migrations.apply(_conn())


def test_a_failing_migration_rolls_back_whole(tmp_path):
    _domain(
        tmp_path,
        "qq",
        {"0001_init.sql": "CREATE TABLE qq_a (id INT);\nINSERT INTO nowhere VALUES (1);\n"},
    )
    conn = _conn()
    with pytest.raises(MigrationError, match="qq/0001_init.sql failed"):
        migrations.apply(conn)
    assert "qq_a" not in _tables(conn)
    assert ("qq", 1) not in {(s.domain, s.version) for s in migrations.status(conn) if s.applied}


def test_variant_file_runs_when_its_predicate_holds(tmp_path, monkeypatch):
    _domain(
        tmp_path,
        "vv",
        {
            "0001_init.sql": "CREATE TABLE vv_main (id INT);\n",
            "0001_init.nofts.sql": "CREATE TABLE vv_fallback (id INT);\n",
        },
    )
    monkeypatch.setitem(migrations.VARIANTS, "nofts", lambda conn: True)
    conn = _conn()
    migrations.apply(conn)
    assert "vv_fallback" in _tables(conn) and "vv_main" not in _tables(conn)
    # The recorded name is the file that ran, so the checksum check stays meaningful.
    assert conn.execute("SELECT name FROM schema_migrations WHERE domain='vv'").fetchone()[0] == (
        "0001_init.nofts.sql"
    )


def test_statement_splitter_keeps_semicolons_in_strings_and_comments():
    sql = "-- a comment; with a semicolon\nCREATE TABLE t (a TEXT DEFAULT 'x;y');\nSELECT 1;\n"
    assert len(migrations.statements(sql)) == 2


def test_the_domain_registry_lists_core():
    assert migrations.registered_prefixes()["core"] == "-"
