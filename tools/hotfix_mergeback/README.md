# Hotfix merge-back

Answers one question before a release is cut: **which fixes are running in
production that the next release from the default branch would silently drop?**

A hotfix made on a release line and never merged back disappears the moment the
next release from the default branch replaces the hotfix tag in production. This
report checks every hotfix (or production) tag, fix by fix.

```bash
# every hotfix tag created in October, across the clones in ~/src
python -m tools.hotfix_mergeback --workspace ~/src --since 2026-10-01 --until 2026-11-01

# what production runs, as a CI gate
python -m tools.hotfix_mergeback --workspace ~/src --production deploy/production.yaml \
  --fail-on-missing -o reports/hotfix-mergeback.md
```

## How a tag is checked

The unit is one `(service, tag)`.

1. **Base.** A hotfix tag (`--hotfix-pattern`, default `vX.Y.Z-hotfix-N`) starts
   from its release tag `vX.Y.Z` when that exists. Hotfix tags are cumulative:
   `-hotfix-2` also carries what `-hotfix-1` carried. Any other tag, or a hotfix
   whose release tag is missing, starts from its merge-base with the default branch.
2. **Default branch.** `--default-branch` when given, else `origin/HEAD`, then
   `main`, then `master`. A remote-tracking branch is preferred over a local one,
   so a stale local branch does not hide a missing fix.
3. **Dropped before matching.** A commit and its revert inside base..tag cancel
   out (a revert of a revert stays, because the change is back). A commit whose
   every touched file matches `non_product_globs.txt` (CI configuration,
   dependency manifests and lock files, docs, test-only files) is **not needed**:
   the product loses nothing without it.
4. **Matching.** The remaining commits are grouped per ticket key; a commit with
   no key is a unit of its own. Each unit runs this chain, first hit wins:

   | Link | Holds when |
   |---|---|
   | same commit | every commit of the unit is an ancestor of the default branch |
   | patch-id | (unkeyed) a commit on the default branch has the same patch-id |
   | key | a commit in base..default names the key. A merge commit is preferred as the evidence: a back-port is a new commit, so its patch-id differs |
   | subject | (unkeyed) a commit in base..default has the same normalised subject (case, conventional prefix, back-port markers and a `(#123)` suffix ignored) |
   | content | every commit's diff applies in reverse to the default tree (`git apply --reverse --check`): the fix is already there, under another ticket or none |

## Verdicts

| Verdict | Meaning |
|---|---|
| MISSING | at least one unit was not found on the default branch |
| BACK-PORTED | every unit was found; the report names the evidence commit per unit |
| NOT-NEEDED | nothing remained after reverts and non-product commits |
| ERROR | no clone, no tag, or no default branch: not checked, never clean |

The default exit status is 0 (report only). `--fail-on-missing` exits 2 on a
MISSING or ERROR verdict, for CI. Exit 1 is bad input.

## What to check

Combinable; the same `(service, tag)` is checked once.

| Option | Selects |
|---|---|
| `--tag REPO:TAG` | one tag (repeatable) |
| `--since`, `--until` | every hotfix tag whose creation date is in `[since, until)`, across the clones in the workspace. A clone whose `origin` repository name differs from its directory is a copy and is skipped (named in the report) |
| `--release-manifest FILE` | every hotfix version pinned in a release manifest |
| `--production FILE` | every ref pinned in a manifest, hotfix or not |

Manifests use the `tools/promotion_diff` format (JSON or YAML, `service: ref` or a
`services:` block with `{ref, repo}`).

## Other options

| Option | Default | Effect |
|---|---|---|
| `--workspace` | `.` | directory holding one clone per service |
| `--clone-base URL` | none | clone a missing service from `URL/<repo>` with `--filter=blob:none` |
| `--hotfix-pattern` | `^(?P<base>v?\d+\.\d+\.\d+)-hotfix-\d+$` | the named group `base` names the release tag |
| `--non-product-file` | `tools/hotfix_mergeback/non_product_globs.txt` | one fnmatch glob per line (`*` crosses `/`) |
| `--key-prefix P` | none | ticket keys are `P-<number>` (repeatable); overrides `--key-pattern` |
| `--key-pattern` | `\b[A-Z][A-Z0-9]{1,9}-\d+\b` | regex for ticket keys |
| `--no-fetch` | fetch | use local refs only; otherwise `git fetch --tags` per clone with a remote |
| `--json` | markdown | every unit with its commits, method and evidence commit |
| `-o FILE` | stdout | write the report to a file |

## Limits

- A fix re-done by hand under a new key, with different content, is reported as
  MISSING. That is the safe direction: a person confirms it.
- The content check needs the blobs; on a blobless clone git fetches them on
  demand from the origin.

Tests: `tests/tools/test_hotfix_mergeback.py` (offline, throwaway git repositories).
