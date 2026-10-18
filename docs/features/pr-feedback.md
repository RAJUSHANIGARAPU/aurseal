# Pull-request feedback

One Markdown summary per pull request, composed only from the run's artefacts, written to
the job's step summary and (when allowed) kept as one PR comment that is updated in place.
The code lives in `traceability/pr_comment.py` and `integrations/github.py` (a ~40-line
`requests` call with `GITHUB_TOKEN`; no third-party action in the supply chain).

## What the summary says

**What it does.** In this order:

1. **Headline.** `RUN TRUST FAILED (<lanes>): THIS RUN IS NOT EVIDENCE` when any lane
   failed its run-trust check: the first line, never a footnote.
2. **Runs:** passed / failed / error / skipped / not run per lane, from the run-trust
   markers.
3. **Detector verdicts:** any `signature_mismatch`, `closed_regressed` or `cleared`, and
   defects that newly reproduce compared with the `main` baseline.
4. **Newly flaky:** the flakiness report diffed against the baseline, so long-known flakes
   do not repeat on every PR. No baseline is said, not assumed.
5. **Requirements:** the matrix headline and every status that changed versus `main`.
6. **Coverage gaps:** new gaps from the gap report.
7. **Generated tests awaiting review:** draft tests and their outcomes.

A missing artefact reads `_not reported_`; it is never rendered as zero. The text is
capped at 60 000 characters (GitHub allows about 65 000); truncation keeps the marker and
headline and ends with a link to the run.

## Posting

- The same Markdown always goes to `$GITHUB_STEP_SUMMARY` (and
  `reports/traceability/pr-comment.md`).
- With `--post`, the comment carrying the hidden marker `<!-- test-run-summary -->` is
  updated in place, or created once.
- **Fork pull requests get the step summary only**: their token is read-only, and the
  `workflow_run` workaround would run with write access next to untrusted code. A missing
  permission is logged, never a build failure.

```bash
python -m traceability pr-comment --reports reports/ --baseline baseline/            # print + files
python -m traceability pr-comment --reports offline-reports/ browser-reports/ \
    --baseline baseline/ --post                                                   # CI
```

| Method | Path | What |
|---|---|---|
| GET | `/api/v1/runs/{run_id}/summary?format=md` | the same rendering for a stored run, for any CI that posts to its own review tool |

## Configuration

| Variable / option | Default | Meaning |
|---|---|---|
| `--baseline` | — | the latest `main` run's reports; without it, "new" sections say there is no baseline |
| `GITHUB_TOKEN` | — | needs `pull-requests: write` to comment |
| `GITHUB_REPOSITORY`, `GITHUB_EVENT_PATH` | set by Actions | repository and PR number (`--pr` overrides) |
| `GITHUB_STEP_SUMMARY` | set by Actions | the summary is appended there |

## Honest limits

"New" needs a baseline from `main`; artefacts expire (7 days today), so an old PR may get
"no baseline". Once the results store holds `main` history, the baseline should come from
there instead.
