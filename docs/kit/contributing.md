# Contributing

## Set up

```bash
git clone https://github.com/RAJUSHANIGARAPU/wiki_project
cd wiki_project
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
pip install -e .
pip install pre-commit && pre-commit install   # ruff, workflow lint and the leak gate on every commit
```

## Where things live

| Path | What |
|---|---|
| `src/testkeel/` | The package: CLI, importers, result model, report, leak gate |
| `src/testkeel/templates/` | The project templates (`_base/` plus one overlay per type) |
| `tests/package/` | The package's tests; collected only when `testkeel` is installed |
| `examples/` | Projects generated from the templates; their own pytest configuration |
| `docs/` | This site; `mkdocs.yml` at the repository root |

## Before you open a pull request

Run what CI runs for the part you changed:

```bash
ruff check . && ruff format --check .
pytest tests/package -n auto          # the package
pytest tests/ -n auto -m "not demo_ui and not draft and not render_check"   # the offline suite
python -m testkeel.leakgate scan --tree .
```

For a documentation change, build the site the way CI does:

```bash
pip install -r requirements-docs.txt
mkdocs build --strict
```

The build fails on a broken link: between pages, to a repository file, into an example
report, or in the README.

## Changing a template

Rendered output is pinned by a snapshot. After an intended change, regenerate it and
commit both together:

```bash
UPDATE_TEMPLATE_SNAPSHOTS=1 pytest tests/package/test_templates_render.py
```

If the change touches a file listed in an example's `.scaffold-manifest`, update the
example in the same commit, or the regeneration check fails.

## Adding a project type

A new type needs an overlay under `src/testkeel/templates/<type>/`, an entry in
`src/testkeel/assignment/types.py` (so `testkeel list-types` shows it), a render case in
the template tests, and a section on the [Templates](templates.md) page. Until all of
them exist it stays `planned`, which `new-assignment` refuses.

## Releases

Version numbers, the deprecation window, the changelog and how a tag reaches PyPI:
[Releasing](releasing.md). A pull request that changes behaviour adds a line to
`CHANGELOG.md` under *Unreleased*.

## Commit messages and pull requests

Short, lower-case, imperative subjects (`feat: add the junit importer`). Pull request
titles and bodies are scanned by the leak gate too.

## Licence

MIT. By contributing you agree that your contribution is licensed the same way.
