---
description: "Open a GitHub pull request for the current branch with a body summarising commits, changed files, the last test results and self-healing activity since the branch left its target. Manual only."
disable-model-invocation: true
---

# create-pr

## Usage

```
/create-pr ["<title>"] [target]
```

Both optional. `target` defaults to `main`; without a title one is derived from the branch.

## Steps

1. **Pre-flight.** Stop with the printed reason, before anything is pushed, when the branch
   is the default or integration branch, `gh` is not authenticated, or the branch has no
   commits ahead of the target:

   ```bash
   python -m tools.slash.pr preflight --target "$TARGET"
   ```

2. **Health snapshot.** Syntax-compile the test modules, then count the JUnit XML already
   in `reports/` (failed = failures + errors; gate `pass` only when nothing failed):

   ```bash
   python -m compileall -q ui/tests api/tests tests
   python -m tools.slash.junit --reports reports
   ```

   No XML means the results are **missing**; say so and suggest `/test-health-report`.
   Never present missing results as zeros.

3. **Healing.** Decisions from the healing session log newer than the branch's fork point:

   ```bash
   python -m tools.slash.healing --fork-point "$TARGET"
   ```

   A missing or empty log, or no entries since the fork point, is reported as
   **not measured** with the reason - never as 0.

4. **Push** with upstream tracking: `git push -u origin "$(git branch --show-current)"`.

5. **Create the PR.** Title = the argument, or `python -m tools.slash.pr title`
   (`feat/search-filters` becomes `feat: search filters`). Body:

   ```bash
   python -m tools.slash.pr body --target "$TARGET" > /tmp/pr-body.md
   gh pr create --base "$TARGET" --title "$TITLE" --body-file /tmp/pr-body.md
   ```

   The body has five sections: Summary (up to 10 commit subjects), Changed files (up to
   20), Test health, Healing, Review checklist.

## Output

The branch pushed, the PR URL, title, target, the test summary and the healing summary (or
"not measured" with its reason). A **BLOCK** test gate is shown first and prominently; the
user decides whether the PR still goes up.

## Stop conditions

Any pre-flight failure. Nothing is pushed in that case.
