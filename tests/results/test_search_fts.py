"""FTS5 search over a fixture store: ranking, filters, incremental reindex."""

from __future__ import annotations

from results.api import dispatch
from results.cli import main
from results.models import CaseRecord
from results.search import export_index, reindex, search
from tests.results._history import add_run


def _seed(store, tmp_path):
    store.upsert_cases(
        [
            CaseRecord(
                "c-pay",
                "tests/test_billing.py::test_pay",
                title="Paying an invoice marks it paid",
                owner="team-pay",
                markers=({"name": "smoke", "args": []},),
                links=(("defect", "BUG-142"),),
            )
        ]
    )
    add_run(
        store,
        "r1",
        1,
        {"timeouts": "failed"},
        messages={"timeouts": "TimeoutError: gateway timeout after 30s"},
        fingerprints={"timeouts": "f-a"},
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "billing.md").write_text(
        "# Billing\n\n## Invoices\n\nInvoices are paid by bank transfer.\n\n"
        "## Refunds\n\nPartial refunds.\n"
    )
    return docs


def test_ranking_types_and_filters(store, tmp_path):
    docs = _seed(store, tmp_path)
    assert reindex(store, docs_dir=docs) > 0
    page = search(store, "invoice")
    assert page.engine == "fts5"
    types = [h.doc_type for h in page.hits]
    assert "case" in types and "doc" in types
    assert page.hits[0].snippet  # highlighted context
    only_docs = search(store, "invoice type:doc")
    assert {h.doc_type for h in only_docs.hits} == {"doc"}
    assert only_docs.hits[0].doc_ref == "billing.md#invoices"
    failures = search(store, "timeout type:failure")
    assert [h.doc_ref for h in failures.hits] == ["r1::tests/test_x.py::timeouts"]
    assert search(store, "paid owner:team-pay").hits[0].doc_ref == "c-pay"
    assert search(store, "paid owner:nobody").hits == ()
    assert search(store, "paid marker:smoke").hits[0].doc_ref == "c-pay"
    assert search(store, "BUG-142 type:defect").hits[0].doc_ref == "BUG-142"
    assert search(store, "timeout outcome:failed").hits
    assert search(store, "timeout since:3650d").hits


def test_incremental_reindex_only_adds_new_failures(store, tmp_path):
    docs = _seed(store, tmp_path)
    reindex(store, docs_dir=docs)
    before = store.conn.execute(
        "SELECT COUNT(*) FROM search_docs WHERE doc_type = 'failure'"
    ).fetchone()[0]
    add_run(store, "r2", 2, {"crash": "error"}, messages={"crash": "RuntimeError: worker died"})
    reindex(store, docs_dir=docs)
    after = store.conn.execute(
        "SELECT doc_ref FROM search_docs WHERE doc_type = 'failure' ORDER BY doc_ref"
    ).fetchall()
    assert before == 1 and [r[0] for r in after] == [
        "r1::tests/test_x.py::timeouts",
        "r2::tests/test_x.py::crash",
    ]


def test_only_the_latest_occurrence_per_fingerprint_is_kept(store, tmp_path):
    add_run(store, "r1", 1, {"t": "failed"}, fingerprints={"t": "same"}, messages={"t": "boom"})
    add_run(store, "r3", 3, {"t": "failed"}, fingerprints={"t": "same"}, messages={"t": "boom"})
    reindex(store, docs_dir=None)
    # A late import of an older run must not replace the newer occurrence.
    add_run(store, "r2", 2, {"t": "failed"}, fingerprints={"t": "same"}, messages={"t": "boom"})
    reindex(store, docs_dir=None)
    rows = store.conn.execute(
        "SELECT doc_ref FROM search_docs WHERE doc_type = 'failure'"
    ).fetchall()
    assert [tuple(r) for r in rows] == [("r3::tests/test_x.py::t",)]


def test_full_reindex_rebuilds(store, tmp_path):
    docs = _seed(store, tmp_path)
    first = reindex(store, docs_dir=docs)
    assert reindex(store, full=True, docs_dir=docs) == first


def test_rest_cli_and_export(store, tmp_path, capsys):
    docs = _seed(store, tmp_path)
    reindex(store, docs_dir=docs)
    body = dispatch(store, "GET", "/search", params={"q": "gateway", "limit": "5"})
    assert body["engine"] == "fts5" and body["hits"][0]["doc_type"] == "failure"
    path = export_index(store, tmp_path / "out")
    assert path.name == "search-index.json" and path.stat().st_size < 5 * 1024 * 1024
    assert main(["search", "gateway"]) == 0
    assert "engine: fts5" in capsys.readouterr().out
    assert (
        main(
            [
                "search",
                "--reindex",
                "--full",
                "--docs-dir",
                str(docs),
                "invoice",
                "--format",
                "json",
            ]
        )
        == 0
    )


def test_the_dashboard_embeds_the_index(store, tmp_path):
    from results.dashboard import render

    docs = _seed(store, tmp_path)
    reindex(store, docs_dir=docs)
    out = tmp_path / "dash"
    render(store, out)
    page = (out / "search.html").read_text()
    assert "Paying an invoice marks it paid" in page
    assert (out / "search-index.json").exists()
