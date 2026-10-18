"""Without FTS5 the same queries return the same hits, and say engine=like."""

from __future__ import annotations

import pytest

import results.search as search_mod
from results.models import CaseRecord
from results.search import index_payload, reindex, search
from tests.results._history import add_run

QUERIES = [
    "invoice",
    "gateway timeout",
    "invoice type:case",
    "timeout type:failure",
    "paid owner:team-pay",
]


def _seed(store):
    store.upsert_cases(
        [
            CaseRecord(
                "c-pay",
                "tests/test_billing.py::test_pay",
                title="Paying an invoice",
                owner="team-pay",
                description="An invoice is paid",
            )
        ]
    )
    add_run(store, "r1", 1, {"gw": "failed"}, messages={"gw": "gateway timeout after 30s"})
    reindex(store, docs_dir=None)


@pytest.mark.parametrize("query", QUERIES)
def test_like_fallback_finds_the_same_documents(store, monkeypatch, query):
    _seed(store)
    fts = {(h.doc_type, h.doc_ref) for h in search(store, query).hits}
    monkeypatch.setattr(search_mod, "fts5_available", lambda conn: False)
    page = search(store, query)
    assert page.engine == "like"
    assert {(h.doc_type, h.doc_ref) for h in page.hits} == fts and fts


def test_the_nofts_migration_variant_works_end_to_end(tmp_path, monkeypatch):
    from results import migrations
    from results.store import open_store

    monkeypatch.setitem(migrations.VARIANTS, "nofts", lambda conn: True)
    store = open_store(str(tmp_path / "nofts.db"))
    sql = store.conn.execute("SELECT sql FROM sqlite_master WHERE name='search_docs'").fetchone()[0]
    assert "VIRTUAL" not in sql.upper()
    _seed(store)
    page = search(store, "gateway")
    assert page.engine == "like" and page.hits[0].doc_type == "failure"
    assert "gateway" in page.hits[0].snippet
    store.close()


def test_index_payload_is_capped(store):
    _seed(store)
    full = index_payload(store)
    capped = index_payload(store, limit_bytes=200)
    assert len(capped["docs"]) < len(full["docs"]) and capped["dropped"] > 0
