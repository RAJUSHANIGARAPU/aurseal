# Release tooling: promotion diff, config preflight, hotfix merge-back, lead time

Four developer tools under `tools/`. None is part of the test run, and all use only
the Python standard library (PyYAML, already a project dependency, only for YAML
manifests). The hotfix merge-back and lead-time reports reuse the promotion diff's
manifest format, git helpers, ticket-key regex and noise file.

| Tool | What it gives you |
|---|---|
| `tools/promotion_diff/promotion_diff.py` | what promoting one environment's versions onto another would ship, and what it would regress |
| `tools/config_preflight/` | which stored records a new version of the workflow config would reject, before it is deployed |
| `tools/hotfix_mergeback/` | per hotfix tag: MISSING / BACK-PORTED / NOT-NEEDED fix by fix, with the evidence commit ([README](../../tools/hotfix_mergeback/README.md)) |
| `tools/release_leadtime/` | median, p90 and threshold counts of merge -> tag -> environment -> production, hotfix vs regular ([README](../../tools/release_leadtime/README.md)) |

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


## 3. Config preflight

### What it does

A config-driven app validates every record against the rules of the state it sits in.
Change the config and the stored records do not change with it: the next upgrade or the
next action rejects them. `python -m tools.config_preflight` asks the question before the
deploy. For every record it evaluates the **new** rules of the record's current state and
reports:

| Finding | Meaning |
|---|---|
| rejected | a rule fails: record, field, rule, stored value, and whether the old config already rejected it |
| orphaned | the record's state, or its entity type, is gone from the new config |
| stale action | the record sits in an action (an open draft) its state no longer offers |
| stale route | the record is headed for a target that is not a declared target of its state; a state with no routes accepts none |
| stuck | the state had a way out in the old config and has none in the new one |
| not assessed | a rule the tool does not understand, a pattern that does not compile, a changed reference target, a record without a state |

Records valid under the new rules are counted, not listed. Not assessed is never a pass.
With an old config the report also lists the config change itself: entity types, states,
transitions and field rules added, removed, tightened or loosened. Transition conditions
are not evaluated; a conditional exit counts as an exit.

The config is the workflow YAML of `generators.workflow_model`. A state may carry its own
`fields` list; an entry there is merged over the entity-level entry of the same name:

```yaml
fields:
  - {name: seats, type: integer, min: 1}
states:
  paused:
    fields:
      - {name: note, required: true}   # only in `paused`
```

Understood rules: `type` (text, integer, number, decimal, amount, boolean, date, datetime,
choice, reference), `required`, `choices`, `pattern` (full match), `min`, `max`, `max_len`,
`ref` (a changed target is not assessed: targets are not resolved). Anything else is not
assessed.

### Sources

| Source | How |
|---|---|
| export | `--records FILE`, `.jsonl` or `.json`; `id`, `type`, `state`, `action`, `target` and a `fields` mapping (keys configurable with `--id-key` etc.) |
| paged lists | with `--base-url`: the committed-records list and the open-drafts list per type, each read to the end by `nextToken` |
| event history | `--events --from DAY --to DAY`: "action entered" events per UTC day, 500 per page, days read in parallel (`--workers`), a timed-out page retried once. Only events whose (state, action) the new config does not declare are kept; each such record is fetched again and the **fetched** record is judged. 404 = gone. Recovers drafts the drafts list no longer shows because their action is gone |
| ids | `--ids FILE`: one `<id>` or `<type> <id>` per line, each fetched and judged; 404 = gone |

The token comes from `--token-file` or the variable named by `--token-env`
(default `CONFIG_PREFLIGHT_TOKEN`), never from the command line.

Config at a ref is read with `git show REF:path`, nothing is checked out: `--config PATH`
for one file or directory, or `--config-template 'config/{type}.yaml'` (repeatable, tried in
order) per type. A type with no config at the ref is **could not determine**, never
skipped. The types come from `--types a,b`, or from a manifest, `--manifest PATH@REF
--target NAME` (`targets: {name: [types]}` or `types: [...]`, matched case-insensitively);
`--types` wins. `--migration-template 'migrations/{type}.sql'` marks a finding
`covered-by-migration?` when every stale name in it appears as a whole word in that type's
script at `--new-ref`: a hint, still a finding.

`--offline-fixtures DIR` answers every GET from `DIR/http/*.json` (keyed by path and
query) and reads config from `DIR/config/<ref>/` instead of git. An unanswered GET is an
error, not an empty page. The tests run this way.

### Exit status

| Code | Meaning |
|---|---|
| 0 | clean: every source read, every type had a config, something was scanned |
| 2 | with `--fail-on-reject`: any finding (not assessed too, unless `--allow-not-assessed`) |
| 1 | undetermined: bad input, a source that failed (403 included), a type with no config, or nothing scanned |

A finding wins over an incomplete scan; the report lists both. The summary table counts
what was scanned per type and source.

### Example

```bash
python -m tools.config_preflight --old-ref v1.4.0 --new-ref HEAD \
  --config-template 'config/{type}.machine.yaml' --types subscription \
  --base-url "$SERVICE_URL" --events --from 2026-03-01 --to 2026-03-07 --fail-on-reject
```

```markdown
Records: 412 read, 409 valid under the new config, 1 rejected, 1 orphaned, 0 stuck,
1 on a stale action, 0 on a stale route, 0 not assessed.

| Entity | drafts | events | records |
|---|---|---|---|
| subscription | 12 | 1 | 399 |

## Stale action (1)

| Record | Entity | State | Name | Why | Source | Verdict |
|---|---|---|---|---|---|---|
| S-4 | subscription | active | pause | action is not offered in state active | events | finding |
```
