# Release tooling: promotion diff

Two developer tools under `tools/`. Neither is part of the test run, and both use only
the Python standard library (the promotion diff needs PyYAML, already a project
dependency, only for YAML manifests).

| Tool | What it gives you |
|---|---|
| `tools/promotion_diff/promotion_diff.py` | what promoting one environment's versions onto another would ship, and what it would regress |

Their tests are offline and run in the normal suite: `pytest tests/tools`.

---


## 2. Promotion diff

### What it does

Before promoting the versions of one environment (FROM) onto another (TO), you want to
know what the promotion ships and what it takes away. Given two version manifests and
local clones of the services, `promotion_diff.py` reports, per service:

- **What ships.** The commits FROM has and TO lacks (merges excluded). Ticket keys come
  out of the subjects through a configurable regex. A commit without a key is either
  **noise**, when its subject matches a pattern in the noise file (dependency bumps,
  release bookkeeping, formatting), or an **unkeyed change**, listed in full. A key wins
  over a noise pattern.
- **What it would regress.** Commits only TO has, typically hotfixes made directly on the
  target. A TO-only commit is cleared when FROM already carries it, in order of
  strength: the same patch (`git cherry`, by patch-id), the same subject, or identical
  content in every file it touched. Anything left is listed under **Would regress**, with
  the tags that contain it.
- **Where the tests are.** With `--test-root`, each key lists the test files that
  mention it. A mention is not an assertion, and the column claims no more than that.

A service that could not be compared (no clone, an unknown ref) is shown as **Not
compared** with the reason, never as unchanged.

### Manifests

JSON or YAML. Either a flat `{service: ref}` mapping or a `services:` block. A ref is
anything `git rev-parse` resolves: a tag, a branch, a sha. `repo` overrides the clone
directory, relative to the workspace.

```yaml
services:
  orders: v1.4.2
  billing: {ref: 3f2c1e0, repo: billing-service}
```

### Configuration

| Option / variable | Default | Effect |
|---|---|---|
| `--from`, `--to` | required | the two manifests; the file stems label the report |
| `--show FILE` | none | extra manifest shown as a column (repeatable) |
| `--workspace` / `PROMOTION_DIFF_WORKSPACE` | `.` | directory holding one clone per service |
| `--noise-file` | `tools/promotion_diff/noise_patterns.txt` | one case-insensitive regex per line, `#` comments |
| `--key-pattern` | `\b[A-Z][A-Z0-9]{1,9}-\d+\b` | regex for ticket keys |
| `--test-root DIR` | none | where to look for tests mentioning a key (repeatable) |
| `--no-fetch` | fetch | use local refs only; otherwise `git fetch --tags` per clone that has a remote, warning on failure |
| `--keys` | off | print only the shipped keys, one per line |
| `--strict` | off | exit 2 when a commit would regress, or a service pinned in both manifests could not be compared |
| `-o FILE` | stdout | write the report to a file |

Exit status: 0 report written, 1 bad input (missing manifest, bad regex, unknown test
root), 2 the `--strict` finding above.

### Example

```bash
python tools/promotion_diff/promotion_diff.py \
  --from deploy/staging.yaml --to deploy/production.yaml \
  --workspace ~/src --test-root tests --strict -o reports/promotion-diff.md
```

```markdown
| Service | staging | production | Commits | Keys | Regress |
|---|---|---|---|---|---|
| orders | v1.1 | v1.0.1 | 5 | 3 | 1 |

## orders

Ships 3 key(s):
- BUG-142: BUG-142 fix rounding (11e50c4a) (tests mentioning it: tests/test_rounding.py)
...
**Would regress 1 commit(s) only production has:**
- BUG-150 hotfix null check (227db636) (in tags: v1.0.1)
```
