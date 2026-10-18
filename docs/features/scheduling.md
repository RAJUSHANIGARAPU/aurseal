# Scheduling

## What it does

A nightly run has one failure mode no test catches: it stops firing. A disabled or
paused scheduled workflow does not produce a red build, it produces no build, and
that reads like a quiet night. Scheduling here is therefore a claim that gets
measured:

- `gate.schedules.load_schedules` reads the `on.schedule` crons a workflow declares,
  and which jobs opt into scheduled runs (a job- or step-level `if:` naming
  `schedule`).
- `gate.schedules.trigger_from_env` names what started the current run: `push`,
  `pull_request`, `schedule`, `dispatch`, `api` or `manual`.
  `schedule_name_from_env` names the cron that fired (from the CI event payload).
  The results store records both on the run row.
- `gate.schedules.schedule_health` judges a schedule by its newest recorded run:
  - **healthy**: the newest run is younger than the maximum age;
  - **stale**: the newest run is older, or the schedule has never fired (never is
    not healthy);
  - **unknown**: there is no history to read, or it cannot be read. This is an
    abstention and is never reported as healthy.
- `gate.ci_provider.GitHubActionsProvider` starts a run from outside CI through the
  `workflow_dispatch` API. It validates the inputs first, so an unknown lane name
  is refused instead of producing a run that selects nothing.

## How to use

```bash
# List declared schedules and the jobs that opt in
python -m gate.schedules

# Health check: exit 0 healthy, 1 stale, 2 unknown (no history), 3 no schedule declared
python -m gate.schedules --check --max-age-hours 36
```

```python
from gate.ci_provider import GitHubActionsProvider

provider = GitHubActionsProvider("owner/repo", allowed_lanes=["slow", "regression"])
provider.dispatch("tests.yml", "main", {"lanes": ["slow"], "browser_matrix": True, "env": "qa"})
```

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SCHEDULE_MAX_AGE_HOURS` | `36` | Age after which the newest scheduled run counts as stale |
| `RUN_TRIGGER` | from `GITHUB_EVENT_NAME`, else `manual` | Explicit trigger for the run row (`push`, `pull_request`, `schedule`, `dispatch`, `api`, `manual`) |
| `GITHUB_TOKEN` | none | Token used by `GitHubActionsProvider` (name configurable with `token_env`) |

History is read from the results store through the named query
`last_run_by_trigger` (`trigger`, optional `schedule_name`, newest first,
`started_at` column). Without a store the check reports **unknown**.

## Example

A workflow with `cron: "17 2 * * *"` whose last scheduled run was recorded 50 hours
ago:

```text
$ python -m gate.schedules --check
Tests:17 2 * * *  lanes: nightly
Tests:17 2 * * *: STALE — last scheduled run 2 days, 2:00:00 ago exceeds 1 day, 12:00:00
$ echo $?
1
```

## Honest limits

- The CI-selection gate ignores step `if:` conditions, so a schedule-only step
  counts as "covered" for pull requests.
- Cron on hosted CI is best effort; a delay of minutes to an hour is normal, which
  is why the default maximum age is 36 hours, not 24.
- Staleness can only be detected by a run that does fire (a push run or the health
  endpoint). A repository with no activity at all detects nothing.
