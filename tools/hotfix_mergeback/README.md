# Hotfix merge-back

Answers one question before a release is cut: **which fixes are running in
production that the next release from mainline would silently drop?**

A hotfix made on a release line and never merged back to mainline disappears the
moment the next mainline release replaces the hotfix tag in production. This
report finds those commits for every service in the production manifest.

```bash
python -m tools.hotfix_mergeback --production deploy/production.yaml \
  --workspace ~/src --fail-on-missing -o reports/hotfix-mergeback.md
```

## What it reports

For each service: every commit reachable from the production ref and not from
mainline (merges excluded). A commit is cleared when mainline already carries it,
judged exactly as `tools/promotion_diff` judges a regression, in order of strength:

1. **same patch**: `git cherry` finds a commit on mainline with the same patch-id
   (a cherry-pick or a clean rebase);
2. **same subject**: a mainline-only commit has the same subject;
3. **identical files**: every file the commit touched has the same content on
   both sides.

What is left is listed under **Not on mainline**, with the ticket keys from the
subject and the tags that contain it. Commits matching the noise file
(dependency bumps, release bookkeeping, formatting) are listed apart as noise and
do not fail the run; a commit naming a ticket key is never noise. The cleared
commits are listed too, with the equivalence that cleared them, so a reviewer
can check the call.

A service that could not be checked (no clone, unknown ref, no mainline) is
**Not checked**, never clean.

## Inputs

| Option | Default | Effect |
|---|---|---|
| `--production FILE` | required | version manifest of production, the `promotion_diff` format (JSON or YAML); the stem labels the report |
| `--mainline` | `main` | branch the next release is cut from; `origin/<name>` is preferred over a local branch of that name, so a stale local branch does not hide a missing fix |
| `--workspace` | `.` | directory holding one clone per service (`repo` in the manifest overrides the name) |
| `--noise-file` | `tools/promotion_diff/noise_patterns.txt` | one case-insensitive regex per line |
| `--key-pattern` | `\b[A-Z][A-Z0-9]{1,9}-\d+\b` | regex for ticket keys |
| `--no-fetch` | fetch | use local refs only; otherwise `git fetch --tags` per clone with a remote, warning on failure |
| `--json` | markdown | machine-readable output with the same content |
| `--fail-on-missing` | off | exit 2 when a non-noise commit is missing from mainline, or a service was not checked |
| `-o FILE` | stdout | write the report to a file |

Exit status: 0 report written, 1 bad input, 2 the `--fail-on-missing` finding.

## Limits

- A fix re-done on mainline by hand, with a different patch, subject and end
  content, is reported as missing. That is the safe direction: a human confirms it.
- Merge commits that exist only on the production line are not listed; their
  parents are.

Tests: `tests/tools/test_hotfix_mergeback.py` (offline, throwaway git repositories).
