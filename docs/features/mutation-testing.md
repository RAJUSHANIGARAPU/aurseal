# Mutation testing

`checks/mutation/` runs [mutmut](https://github.com/boxed/mutmut) 3.x on a deliberately small
scope and fails when a package's mutation score drops below its threshold. A coverage
percentage says code was executed; a mutation score says the tests would notice if it were
wrong. The `mutation-testing` plugin is a thin adapter over it.

## What it does

* **Scope and thresholds** live in `checks/mutation/mutation.toml`
  (`[tool.wiki_checks.mutation]`; the same table works in a `pyproject.toml`): per package the
  `source_paths`, optional `only_mutate` globs, the `tests` that must kill the mutants, a
  `threshold`, and `deselect` for tests that cannot run under mutmut. A package without a
  numeric threshold is rejected: it could not fail.
* **mutmut 3 interface.** mutmut 3 is configured only through `[tool.mutmut]` in the working
  directory (its CLI has no `--paths-to-mutate`). Each package runs in a fresh copy of the
  repository under `reports/mutation/work/` with a generated `[tool.mutmut]` table, so the
  checkout and its `pyproject.toml` are never touched. The mutant runs drop the repository's
  `addopts` (report directories, browser channel).
* **Counts** come from `mutmut results --all true` (one `<mutant>: <status>` line per mutant),
  cross-checked with `mutmut export-cicd-stats`. Progress bars and emoji summaries are ignored,
  not mined for numbers.
* **Score** = (killed + timeout + segfault + caught by type check) / (that + survived + no
  tests). A timeout means the tests noticed; a mutant no test runs counts **against** the score.
* **Verdict:**

| What happened | Status |
|---|---|
| every selected package at or above its threshold (exactly at passes) | `pass` |
| a package below its threshold | `fail`, naming the package and surviving mutants |
| mutmut missing, its run failed, no mutants, mutants left unchecked, totals disagree | `unknown` |
| bad configuration, unknown package | `error` |
| dry run | `skip` |

* Metric rows: `metric_mutation_score` per package and `metric_mutation_survivor` (top 20).

## How to use

```bash
pip install -r requirements-checks.txt                 # mutmut==3.8.0
python -m checks.mutation                              # every configured package
python -m checks.mutation --package checks             # one package
# exit 0 = at/above threshold, 1 = below, 2 = no score
```

## Configuration

```toml
[tool.wiki_checks.mutation]
max_children = 4          # parallel mutant workers
timeout_s = 3600          # per package
copy_repository = true    # copy repo files next to the mutants (tests read them)

[tool.wiki_checks.mutation.packages.verification]
source_paths = ["verification"]
tests = ["tests/verification"]
deselect = ["tests/verification/test_known_defect.py::test_..."]  # spawn a subprocess
threshold = 0.55
```

Thresholds start at the first measured score minus a small margin and are only ratcheted
**up**. Mutation runs take minutes, so they belong in the nightly job, never on pull requests.

## Example

First measured scores (mutmut 3.8.0, this repository): see the comment block at the top of
`checks/mutation/mutation.toml`, which records each package's measured score and the threshold
derived from it.

## Honest limits

Scores are comparable only within the same scope and mutmut version. Tests that start a fresh
Python process cannot see mutated code and are deselected from the killing suite (they still run
in the offline suite). mutmut runs on Linux and macOS only.
