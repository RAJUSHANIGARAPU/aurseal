# Generated-test review

A generated test is a **draft** until a person approves it in a reviewed pull request.
While it is a draft it can neither redden nor green the gate, and its results are never
evidence. The code lives in `traceability/review.py`; the lane rule in `gate/lanes.py`.

## The marker

**What it does.** Every generator writes a module-level marker into the files it emits:

```python
# Generated test awaiting review: remove the draft marker in a reviewed PR to approve.
pytestmark = pytest.mark.draft(
    generator="spec",
    source="specs/subscription.md",
    generated_at="2026-01-01T00:00:00.000Z",
)
```

| Generator | `generator=` | Where it is added |
|---|---|---|
| `TestGenerator.generate_from_trace` (and `GeneratorAgent`, `PlannerAgent`) | `trace` | returned test source |
| `TestGenerator.generate_from_spec` | `spec` | returned test source |
| `TestGenerator.generate_from_workflow` (`python -m generators.cli generate`) | `workflow` | the test module, not the page object |
| `TestGenerator.from_recording` (`generators.cli convert`) | `recording` | the test module |
| `web_discovery` `ScriptGenerator` | `web-discovery` | every `test_wd_*.py` |
| Postman `GenerationAgent` | `postman` | every generated module |

`generators.yaml_suite` is not on the list: it parametrises tests at collection time from
reviewed workflow YAML and writes no files.

`mark_draft(code, generator, source)` adds the block (and `import pytest` when missing),
is idempotent, and keeps a module's own `pytestmark`. `unmark_draft(code)` removes exactly
that block: what a reviewer does to approve.

## Approval

- **Approve:** remove the marker block in a pull request that someone reviews. Git history
  is the approval record. With branch protection, a `CODEOWNERS` entry for the generated
  paths makes that review mandatory.
- **Reject:** delete the file.

## What the state changes

1. `draft` is a default-gate exclusion (`gate.lanes.excluded_markers()`), so the gate
   command carries `not draft`.
2. Drafts still run, in their own CI job that is not a required check, so they stay
   visible (the PR comment lists their results).
3. Draft results never count as evidence: a requirement backed only by drafts is
   `DRAFT_ONLY` in the matrix, plans have `no_draft_evidence`, the release verdict treats
   `DRAFT_ONLY` as no evidence. The traceability plugin writes `review_state: draft` per
   test into `case-links.json`; the store sees the `draft` marker on the result.

## Lint

```bash
python -m traceability review lint                 # GENERATED_TEST_PATHS, default generated_tests/
python -m traceability review lint ui/tests/generated
python -m traceability review queue               # every collected test awaiting review
```

`review lint` fails (exit 1) on a test file under a generator output path that has
neither the marker nor a commit in its history that removed one: a generator that forgot
to mark. Without git it reports that it cannot verify, never a pass.

| Method | Path | What |
|---|---|---|
| GET | `/api/v1/review/drafts` | catalogue cases carrying the marker (read-only; approval stays in the PR) |

(The design's `GET /cases?review_state=draft` would need a filter inside the data layer's
case listing; a separate read-only route keeps that handler untouched.)

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `GENERATED_TEST_PATHS` | `generated_tests` | generator output paths to lint (`os.pathsep` separated) |

## Honest limits

On a repository without branch protection, "reviewed PR" can be self-review: the
mechanism makes the state explicit, it cannot create a second reviewer. A
non-required job is easy to ignore, which is why the PR comment lists draft failures.
