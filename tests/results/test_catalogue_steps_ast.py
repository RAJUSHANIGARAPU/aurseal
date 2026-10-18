"""Steps come from literal allure.step strings, found statically; nothing is executed."""

from __future__ import annotations

from pathlib import Path

from results.catalogue import build_catalogue, extract_steps

pytest_plugins = ["pytester"]


def sample(allure, step, name):  # pragma: no cover - only parsed, never called
    with allure.step("Open the page"):
        with allure.step(f"Search for {name}"):
            pass
    with step("Check the result"):
        pass
    with open("x") as handle, allure.step("Close " + "it"):
        handle.read()


def test_literal_steps_in_source_order_and_dynamic_ones_marked():
    assert extract_steps(sample, None) == [
        "Open the page",
        "<dynamic>",
        "Check the result",
        "<dynamic>",
    ]


def test_a_docstring_steps_block_wins_over_ast_steps():
    doc = "Title.\n\nSteps:\n1. First\n2) Second\n"
    assert extract_steps(sample, doc) == ["First", "Second"]


def test_no_function_and_no_block_means_no_steps():
    assert extract_steps(None, "Just a title.") == []


def test_building_the_catalogue_never_runs_a_test_body(pytester):
    marker = pytester.path / "ran.txt"
    pytester.makepyfile(
        test_side=f"""
import pathlib

def test_writes():
    pathlib.Path({str(marker)!r}).write_text("ran")
"""
    )
    cat = build_catalogue(pytester.path)
    assert [c.qualname for c in cat.cases] == ["test_writes"]
    assert not Path(marker).exists()
