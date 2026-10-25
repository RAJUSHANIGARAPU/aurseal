# Security testing

`checks/security/` runs three real tools behind one verdict that can fail. The `security-scan`
plugin is a thin adapter over it. It replaces a regex grep (false positives on test tokens,
blind to real ones) and a pip-audit call whose result never changed the status.

## What it does

| Tool | Looks at | Blocks on |
|---|---|---|
| **gitleaks** 8.30.1 (binary) | the whole git history (`gitleaks git`), secrets redacted in output | any leak not allowed by `.gitleaks.toml` |
| **bandit** 1.9.4 | first-party packages (top-level directories with `__init__.py`; `tests/`, `.venv`, generated code excluded) | medium+ severity at medium+ confidence |
| **pip-audit** 2.10.1 | `requirements-lock.txt`, `-checks`, `-platform` and `-docs`, exactly as pinned (`--no-deps --disable-pip`) | any known vulnerability, with or without a fixed version |

Every finding is normalised to `SecurityFinding(tool, rule_id, severity, location, fingerprint)`.
Fingerprints are stable: bandit's uses the rule, file and a hash of the flagged line (not its
line number), pip-audit's the package and vulnerability id.

**Allow-list with expiry** (`security/allowlist.yml`): each entry needs `fingerprint`,
`reason` and `expires`; a missing field is rejected when the file loads. An expired entry
exempts nothing *and* fails the check on its own, so an exemption cannot become permanent by
neglect. Entries that match nothing are listed so they can be deleted.

Secrets that are deliberately fake (redaction tests) are excused in `.gitleaks.toml` by exact
value **and** file, so the same value anywhere else still fails.

**Verdict:**

| What happened | Status |
|---|---|
| a blocking finding, or an expired allow-list entry | `fail` |
| a requested tool missing, crashed, offline, or wrote no report | `unknown` |
| every requested tool ran, nothing blocks | `pass` |
| dry run | `skip` |

Metric rows: `metric_security_scan` (one per tool that ran: version, findings, blocking,
exit code) and `metric_security_finding` (one per finding, with allow-list state).

## How to use

```bash
pip install -r requirements-checks.txt          # bandit, pip-audit
python -m checks.security --tools gitleaks,bandit,pip-audit
# exit 0 = clean, 1 = blocking finding / expired exemption, 2 = a tool gave no answer
gitleaks git . --config .gitleaks.toml --redact --no-banner   # the CI secrets step (secret-scan.yml)
```

```python
from checks.security.scan import scan

outcome = scan(["bandit", "pip-audit"])
```

Plugin context: `tools`, `source_dir`, `lockfile`, `allowlist`, `metric_sink` /
`record_metrics`, `dry_run`.

## Configuration

| Setting | Default | Meaning |
|---|---|---|
| `GITLEAKS_BIN` | `gitleaks` on `PATH` | gitleaks binary |
| `security/allowlist.yml` | committed | expiring exemptions |
| `.gitleaks.toml` | committed | default gitleaks rules + scoped exceptions |
| `--lockfile` | `requirements-lock.txt` | what pip-audit audits |
| `METRICS_DIR` | `reports/metrics` | where metric rows go |

## Example

First real run on this repository (gitleaks 8.30.1, bandit 1.9.4, pip-audit 2.10.1):

* gitleaks: 7 hits, all two invented tokens in redaction tests, now excused by value and file
  in `.gitleaks.toml`; 0 after that.
* bandit: 14 medium+ findings in existing code (sha1 used for names, XML parsing without
  `defusedxml`, f-string SQL with fixed table names, `urlopen`, a deliberate RTL character in
  edge-case data). Each is in `security/allowlist.yml` with its reason and fix, expiring
  2027-01-16.
* pip-audit: 13 known vulnerabilities in 6 pinned packages, all with fixed versions. These are
  **not** exempted: the check fails until the lock file is upgraded.

## In CI

The secret scan is its own workflow, `.github/workflows/secret-scan.yml`: every push on
every branch (bring-up and feature branches included) and every pull request, a root job
with no `if:` and no `needs:`, a full-history checkout, the pinned and checksum-verified
gitleaks binary, redacted output, and an unmasked exit status. `gate.workflow_lint` fails a
change that breaks any of those (see [CI workflow](ci-workflow.md#secret-scan)). SAST and
the dependency audit stay in the `security` job of `tests.yml`.

## Honest limits

SAST on a test framework mostly finds test-tooling patterns; the first triage is recorded in the
allow-list with reasons. pip-audit queries a public vulnerability database, so it needs network
(right for a security job, wrong for the offline suite, which never runs it). A dynamic scan
(DAST) is not part of this: the demo service has little attack surface, and no DAST tool was
chosen.
