"""The documentation site's link hooks: rewriting, and the README check, without MkDocs."""

from __future__ import annotations

from pathlib import Path

import yaml

from tools.docs_site.hooks import (
    github_slug,
    headings,
    links,
    readme_link_problems,
    rewrite_links,
)

ROOT = Path(__file__).resolve().parents[2]
SITE = "https://owner.example.test/project/"
REPO = "https://git.example.test/owner/project"


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "docs" / "kit").mkdir(parents=True)
    (tmp_path / "docs" / "index.md").write_text("# Home\n", encoding="utf-8")
    (tmp_path / "docs" / "kit" / "quickstart.md").write_text("# Quickstart\n", encoding="utf-8")
    (tmp_path / "docs" / "samples").mkdir()
    (tmp_path / "examples" / "web-ui-demo").mkdir(parents=True)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "README.md").write_text("# Pkg\n\n## Usage notes\n", encoding="utf-8")
    return tmp_path


def test_slugs_follow_the_repository_host():
    assert github_slug("12. Markers & Segmentation") == "12-markers--segmentation"
    assert github_slug("10. AI Skills (Slash Commands)") == "10-ai-skills-slash-commands"
    assert github_slug("The `report` command") == "the-report-command"


def test_headings_skip_code_and_number_duplicates():
    text = "# Title\n\n```bash\n# not a heading\n```\n\n## Run\n\n## Run\n"
    assert headings(text) == {"title", "run", "run-1"}


def test_links_outside_code_only():
    text = "[a](x.md) ![b](img.png)\n```\n[c](hidden.md)\n```\n"
    assert links(text) == ["x.md", "img.png"]


def test_a_link_leaving_docs_points_at_the_repository_host(tmp_path):
    root = _repo(tmp_path)
    page = root / "docs" / "kit" / "quickstart.md"
    text = "[pkg](../../pkg/README.md#usage-notes) and [samples](../samples/)"
    out, problems = rewrite_links(text, page, root / "docs", root, REPO, "kit/quickstart/")
    assert problems == []
    assert f"({REPO}/blob/main/pkg/README.md#usage-notes)" in out
    assert f"({REPO}/tree/main/docs/samples)" in out


def test_links_between_pages_are_left_to_mkdocs(tmp_path):
    root = _repo(tmp_path)
    page = root / "docs" / "kit" / "quickstart.md"
    text = '[home](../index.md "Home") [web](https://example.test/) [top](#quickstart)'
    out, problems = rewrite_links(text, page, root / "docs", root, REPO, "kit/quickstart/")
    assert (out, problems) == (text, [])


def test_a_report_link_lands_relative_to_the_page_url(tmp_path):
    root = _repo(tmp_path)
    nested = root / "docs" / "kit" / "quickstart.md"
    out, problems = rewrite_links(
        "[r](../reports/web-ui-demo/)", nested, root / "docs", root, REPO, "kit/quickstart/"
    )
    assert (out, problems) == ("[r](../../reports/web-ui-demo/)", [])
    top = root / "docs" / "index.md"
    out, _ = rewrite_links("[r](reports/web-ui-demo/)", top, root / "docs", root, REPO, "")
    assert out == "[r](reports/web-ui-demo/)"


def test_broken_targets_are_reported(tmp_path):
    root = _repo(tmp_path)
    page = root / "docs" / "kit" / "quickstart.md"
    text = "[a](../../missing.py) [b](../reports/no-such-example/)\n```\n[c](../../nope.md)\n```\n"
    _, problems = rewrite_links(text, page, root / "docs", root, REPO, "kit/quickstart/")
    assert len(problems) == 2
    assert problems[0].startswith("../../missing.py")
    assert problems[1].startswith("../reports/no-such-example/")


def test_the_readme_check_can_fail(tmp_path):
    root = _repo(tmp_path)
    readme = root / "README.md"
    readme.write_text(
        "# Project\n\n## Quickstart\n\n"
        "[ok](#quickstart) [bad](#nowhere) [file](pkg/README.md#usage-notes) "
        "[gone](missing.md) [anchor](pkg/README.md#absent) "
        f"[page]({SITE}kit/quickstart/) [report]({SITE}reports/web-ui-demo/) "
        f"[no page]({SITE}kit/absent/) [no report]({SITE}reports/other/) "
        "[elsewhere](https://example.test/anything)\n",
        encoding="utf-8",
    )
    problems = readme_link_problems(readme, root, root / "docs", SITE)
    assert [p.split(":", 1)[0] for p in problems if not p.startswith("http")] == [
        "#nowhere",
        "missing.md",
        "pkg/README.md#absent",
    ]
    assert sum(p.startswith(SITE) for p in problems) == 2


def test_every_readme_link_resolves():
    site_url = yaml.safe_load((ROOT / "mkdocs.yml").read_text(encoding="utf-8"))["site_url"]
    problems = readme_link_problems(ROOT / "README.md", ROOT, ROOT / "docs", site_url)
    assert problems == []
