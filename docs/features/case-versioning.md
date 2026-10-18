# Case versioning

Every result row carries the revision of the test that produced it, so a history can
tell "the product changed" from "the test changed". The code lives in
`results/revision.py`; revisions are stored in `case_revisions` and stamped on
`test_results.revision_id`.

## Revision hash

**What it does.** A case's revision is git's blob hash of the test function's own
normalised source: `inspect.getsource` (decorators included), dedented, CRLF changed to
LF, trailing whitespace and surrounding blank lines removed, then
`sha1("blob <len>\0" + text)`. `git hash-object --stdin` on the same text gives the same
id, without running git.

Each revision also records:

- `file_blob_sha` — `git hash-object` of the whole test file, which catches module-level
  fixture and helper changes;
- `git_sha` — the run's commit: `GITHUB_SHA`, else `.git/HEAD` resolved by reading files
  (loose refs, `packed-refs`, worktrees). No subprocess, so a CI container without git
  still works;
- `first_seen_run_id` — the run that first executed this revision;
- the case's steps at that revision.

**Reading a trend:**

| Outcome | Revision | File | Most likely |
|---|---|---|---|
| changed | same | same | the product or the environment changed |
| changed | same | changed | a helper or fixture in the same file changed |
| changed | changed | — | the test changed |

**Not covered.** Fixtures in `conftest.py`, page objects and helpers in other files are
not part of the hash, so a shared fixture change looks like a product change. Compare the
runs' `git_sha` to narrow it down. A deep hash over imported local modules was rejected:
nearly every commit would change it.

## How it is stamped

Where tests run, the results plugin computes the revision with the catalogue case and
attaches it to the setup report; the controller writes a new `case_revisions` row the
first time a hash is seen and stamps every result row with `revision_id`. A catalogue
build stores the current revision of every collected case. A case's
`current_revision_id` follows the newest revision seen.

## Command line and REST

```bash
python -m results case revisions c-1f2e3d4c5b6a7980
# 3f0c1a9e2b7d  first seen local-42  runs 12 (local-42 .. gh-9001-1)  git 8a1b2c3d4e5f
```

`GET /cases/{case_id}/revisions` returns the revisions, oldest first, each with
`first_run_id`, `last_run_id` and `run_count`.

## Python interface

`function_revision(func)`, `blob_sha(text)`, `file_revision(path)`,
`run_git_sha(rootdir)`, `revisions_for_case(store, case_id)`,
`revision_ranges(store, case_id)`.

## Configuration

None of its own. `GITHUB_SHA` takes precedence over `.git` when set.
