# When is a green run evidence?

A green build says "nothing failed". It does not say "enough was checked". A run that
selected nothing, skipped most of what it selected, or ran detectors that never reached a
verdict also finishes without a failure. This framework treats "is this run evidence?" as a
question with its own answer, in layers. Examples below are from the in-repo demo service.

## 1. An empty selection fails

pytest exits 5 when a marker expression selects nothing. No workflow masks that status
(`gate.workflow_lint` fails `|| true` after pytest), and `tests/test_ci_selection.py` fails
when a collected test is selected by no CI command. A typo such as `-m smokee` is a red
run, not a green one.

## 2. Verdicts have more than two values

A test can pass, fail, or abstain (skip because the environment could not answer). An
abstention is reported as such, never as a pass: `gate.requirement` turns it into a failure
in a lane whose purpose is to run (bring-up branches, the render lane with
`RENDER_REQUIRED=true`, binding tests with `EXT_CONFIG_REQUIRED=true`).
`verification.evidence_verdict.EvidenceVerdict` records whether a check *proved* its claim or only
ran ([verdicts](features/verdicts.md#3-coverage-verdicts-evidenceverdict)).

## 3. Expected-red detectors report a verdict, not a pass

An open defect is not hidden with a skip. A detector (`still_reproduces`, or the
`known_defect` marker) runs the positive oracle: while the defect is open the oracle fails
and the detector reports `reproduces`; when it is fixed it reports `KNOWN DEFECT CLEARED`.
A positive control proves the check exercised the code path at all. Demo example: the
document estate in `tests/demo_examples/documents/` plants a dropped purpose line (demo
fault `doc_omits_purpose_line`) and asserts both outcomes.

## 4. Run trust is a gate, not a report

At session end `gate.integrity` judges the run on four independent rules — executed floor,
skip ratio, detector verdicts reached, fixture provenance — and writes
`reports/run-trust[-<lane>].txt`. With `RUN_TRUST=fail` a FAIL verdict fails the job even
when every test passed. A rule with nothing to judge is listed as absent, which is not a
pass. Details: [run gate](features/run-gate.md#run-trust).

## 5. Nothing silently missing

* The nightly writes which opt-in jobs it could not run, and says so when it is switched off
  ([nightly](ci-decisions.md#nightly)).
* An unbound external configuration produces a warning and an UNSTABLE lane verdict
  ([external config](ci-decisions.md#external-config)).
* The publish job lists every job that ran but left no evidence
  ([evidence publishing](ci-decisions.md#evidence-publishing)).
* A red or not-evidence default-branch run sends one message when a webhook is configured
  ([notification](ci-decisions.md#notification)).

## How to read a run

1. The top of the step summary: each lane's run-trust first line. `VERDICT=PASS` is the
   only line that makes the green evidence; `UNJUDGED` means nothing was in scope.
2. The per-lane table: tests, failures, errors, skipped. A lane with many skips and a PASS
   verdict stayed under its thresholds; check `RUN_TRUST_MAX_SKIP_RATIO` if that surprises.
3. `absent:` and `skipped:` lines: a job that ran and uploaded nothing is a dead lane.
4. Detector verdicts in `reports/verdicts/`: `reproduces` is expected red, `cleared` means
   a fix landed and the detector should graduate.

## What it does not cover

* A check that asserts the wrong thing passes as confidently as one that asserts the right
  thing; run trust counts answers, it cannot judge them. Mutation testing
  ([mutation testing](features/mutation-testing.md)) is the tool for that.
* A third-party site changing under the browser lane is reported as a failure of that lane,
  which is why its enforcement starts at `unstable`.
* Thresholds are fixed, not learned; a lane that is narrow on purpose may need its own.
