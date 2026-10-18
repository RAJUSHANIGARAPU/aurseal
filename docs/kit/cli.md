# CLI reference

Taken from `testkeel --help` and each command's `--help` (version 0.1.0).
`tests/test_kit_docs.py` fails when an option here and the installed command disagree.

```text
Usage: testkeel [OPTIONS] COMMAND [ARGS]...
```

| Command | What it does |
|---|---|
| `testkeel version` | Print the installed version. |
| `testkeel list-types` | Assignment types: available now, or planned. |
| `testkeel new-assignment` | Render a new assignment project, leak-gate it, then write it. |
| `testkeel report` | Render a self-contained HTML summary of one or more result files. |
| `testkeel assignment check` | Run the leak gate over an existing project. |
| `testkeel assignment update` | Merge template changes from a newer release into an `--updatable` project. |

Every command accepts `--help`. `testkeel --version` prints the version and exits, the same
as `testkeel version`.

## `testkeel version`

No options. Prints the version, for example `0.1.0`.

## `testkeel list-types`

| Option | Meaning |
|---|---|
| `--json` | Machine-readable output. |

Output today:

```text
web-ui       available Browser UI tests with Playwright: page objects, components, offline tier (needs browser)
api-rest     available REST API tests with httpx and pydantic: client, services, schema checks
api-graphql  available GraphQL API tests with gql: operations, schema snapshot, offline tier
a11y         available Accessibility checks with axe and Playwright: baseline, planted-violation controls (needs browser)
strategy     available A test plan and strategy document set, no code
perf-k6      available Load tests with k6: smoke, load and stress profiles, SLO thresholds, target guard (needs k6)
perf-locust  available Load tests with Locust: user classes, load shapes, SLO checks, target guard
mobile       available Android app tests with Appium (UiAutomator2): screen objects, offline tier (needs android-sdk)
fullstack    available web-ui and api-rest in one project: UI tests seeded through the API (needs browser)
```

## `testkeel new-assignment`

| Option | Value | Default | Meaning |
|---|---|---|---|
| `--type` | text | | See `list-types`. |
| `--name` | text | | Project name (free text, slugged). |
| `--company` | text | | Only rendered into the README. |
| `--task-file` | path | | Task brief, copied to `.task/`. |
| `--target-url` | text | per type | Web app under test. |
| `--api-base-url` | text | per type | API under test. |
| `--python` | text | `3.12` | 3.11, 3.12 or 3.13. |
| `--report` | text | `html` | html, allure or both. |
| `--ci` | text | `github` | github or none. |
| `--docker` / `--no-docker` | flag | `--docker` | Dockerfile and compose file. |
| `--git` / `--no-git` | flag | `--git` | `git init`, no commit. |
| `--credit` / `--no-credit` | flag | `--no-credit` | One README footer line naming testkeel. |
| `--lock` / `--no-lock` | flag | `--lock` | Run `uv lock` against PyPI only. |
| `--updatable` / `--no-updatable` | flag | `--no-updatable` | Keep `.copier-answers.yml`, naming the public repository and the release tag as the template source. |
| `--output-dir` | path | `./<slug>` | Where to write. |
| `--dry-run` / `--no-dry-run` | flag | `--no-dry-run` | List what would be written. |
| `--force` / `--no-force` | flag | `--no-force` | Write into a non-empty dir. |
| `--non-interactive` / `--no-non-interactive` | flag | `--no-non-interactive` | Never prompt. |

Which URL option each type takes, and its default, is in
[Templates](templates.md#which-url-each-type-takes). An option the type does not take is a
usage error, and `fullstack` needs both.

What it does, in order, all inside a staging directory under `~/.cache/testkeel/`:

1. validate the options;
2. render the templates;
3. `uv lock`, with PyPI as the only index (uv's config files and `UV_INDEX*` variables are
   ignored, so a private registry never lands in `uv.lock`). Skipped with a notice when
   there is no `pyproject.toml`, when `uv` is not on `PATH`, or with `--no-lock`;
4. the [leak gate](leak-gate.md);
5. a self-check: every `.py` file compiles, and `ruff check` passes when ruff is
   installed (skipped with a notice when it is not). Nothing is written into the tree;
6. move the tree to the output directory, then `git init` unless `--no-git`. It writes no
   `user.name` or `user.email` and makes no commit. It prints the identity git will use
   for your first commit, or, when that identity matches your denylist, only a warning
   (never the identity or the entry), so you can set another one for this repository.

By default a project carries no `.copier-answers.yml`: Copier would record where the
templates were installed, a path on your machine. `--updatable` keeps the file and
records `https://github.com/RAJUSHANIGARAPU/wiki_project` at the tag
`testkeel-v<version>` instead, so the project says where it came from, and
[`testkeel assignment update`](#testkeel-assignment-update) can bring template changes
from a later release into it. Run that command, not `copier update`: the templates are
rendered in several passes (common skeleton, then the type), which `copier update` does
not replay.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | Created. |
| 1 | Unexpected error. |
| 2 | Usage error, including a URL option the type does not take, a missing required URL, or a `planned` type. |
| 3 | Output directory not empty (use `--force`). |
| 4 | Leak gate failed; nothing was written. |
| 5 | `uv lock` or the self-check failed; nothing was written. The failing step and the path of the kept staging directory are printed. |
| 6 | Missing prerequisite. |
| 130 | Interrupted. |

## `testkeel report`

```text
Usage: testkeel report [OPTIONS] FILES...
```

`FILES`: one or more report files in any [importer](importers.md) format (required).

| Option | Value | Default | Meaning |
|---|---|---|---|
| `--output`, `-o` | path | `site` | Directory to write. |
| `--title` | text | `Test report` | Page title. |
| `--format` | text | detected | Skip format detection; an [importer](importers.md) name. |

Exit codes: 0 written, 2 usage error, 3 unreadable input or zero tests found.

## `testkeel assignment check`

```text
Usage: testkeel assignment check [OPTIONS] [PATH]
```

`PATH`: project directory, default `.`.

| Option | Meaning |
|---|---|
| `--json` | Machine-readable findings. |
| `--strict` / `--no-strict` | A missing denylist fails (default `--no-strict`). |
| `--allow-host` | A host the project may name, such as its target (repeatable). |

It runs the same rules as `new-assignment`, including the
[project rules](leak-gate.md#project-rules). The project's own target is not known to the
check, so name it with `--allow-host` or in `~/.config/testkeel/allow-hosts.txt`.

Exit codes: 0 clean, 2 cannot run, 4 findings.

## `testkeel assignment update`

```text
Usage: testkeel assignment update [OPTIONS] [PATH]
```

`PATH`: project directory, default `.`. The project must have been made with
`--updatable` and must be a git repository with nothing uncommitted.

| Option | Meaning |
|---|---|
| `--to` | Release tag to update to. Default: the installed version's tag, `testkeel-v<version>`. |
| `--source` | Template repository (URL or path). Default: the `_src_path` recorded in `.copier-answers.yml`. |

The usual flow: upgrade testkeel, then, in the project,

```bash
testkeel assignment update
git diff            # review
git add -A && git commit -m "chore: update the scaffold to testkeel-v0.2.0"
```

The command fetches the recorded tag and the target tag from the template repository,
renders the project's type with its recorded answers at both, and applies the
difference to the project file by file, like `copier update` would:

- a file the template did not change between the two tags is left alone, however you
  changed it;
- a file you did not change takes the new version;
- a file both you and the template changed is merged with `git merge-file`; where the
  two edits touch the same lines, conflict markers (`<<<<<<< yours` ...
  `>>>>>>> testkeel-v<new>`) are left in the file and the command lists it as `CONFLICT`;
- a new template file is added; a file the template removed is deleted only if you never
  changed it;
- files the template never rendered (your tests, `uv.lock`, `.task/`) are not touched.

`_commit` in `.copier-answers.yml` moves to the new tag. Nothing is committed: review
with `git diff`, undo the whole update with `git checkout -- . && git clean -fd`. When
`pyproject.toml` changed, run `uv lock` before you commit. Both renders use the installed
testkeel's render passes over each tag's template files; no code from the fetched tags
runs. Why it works this way: [templates](templates.md#updating-a-project).

Exit codes: 0 updated (or already at the target), 1 unexpected error, 2 usage error (not
`--updatable`, not a git repository, uncommitted changes), 3 updated with conflicts to
resolve, 6 git missing, or the template repository or tag cannot be fetched.

## The leak gate on its own

The gate also has a module entry point, used by this repository's CI:
`python -m testkeel.leakgate scan --help`. See [Leak gate](leak-gate.md).
