# Requirements sync

Requirements live in the repository YAML (`requirements/requirements.yaml`) by default.
When a team also keeps them as GitHub issues, the sync merges every source into one
snapshot, `requirements/snapshot.json`, that the marker check reads, so validation never
needs the network and stays identical in offline CI. Each sync reports what changed and
which tests now point at nothing. The code lives in `integrations/requirements/base.py`
and `traceability/requirements_sync.py`.

## Sources

| Source | What it reads |
|---|---|
| `local` (`LocalYamlSource`) | the repository YAML (the default and, alone, authoritative) |
| `github` (`GitHubIssuesSource`) | issues labelled `requirement` (any state) |

GitHub mapping: the id is the title prefix (`REQ-12: Refund the unused days`), or
`<number-prefix>-<issue number>` with `--number-prefix` (add that prefix to
`REQUIREMENT_ID_PREFIXES`); issues with neither are skipped and listed. A milestone
becomes the release label, a `priority:must` label the priority, and an issue **closed as
not planned** is retired (closed as completed stays active: it is a requirement that
holds). Sources merge in the order given; the same id with different content from two
sources is an error, never a silent pick.

## What a sync reports

Against the last snapshot (none: everything is new to it):

- **added**, **retitled**, **changed** (title, priority or status changed: linked tests
  need a re-review; flagged only, nothing automated), **retired**, **vanished**;
- **orphaned links**: tests whose `requirement` marker points at a retired or vanished id.

## How to use

```bash
python -m traceability requirements sync                       # dry run, local only
python -m traceability requirements sync --source local --source github --write \
    --pr-body reports/traceability/requirements-sync.md
python -m traceability requirements show                       # the registry in use
```

The sync is a dry run unless `--write`. `--pr-body` writes the diff as Markdown for a pull
request; `--db` stores the sync (and the written requirements) in the results store.
A scheduled job commits a changed snapshot through a pull request so every requirement
change is a reviewable diff; this repository ships the job as a snippet, it does not open
pull requests by itself.

`Registry.load()` prefers `requirements/snapshot.json` over the YAML when the snapshot
exists, so commit a snapshot only when an outside source is configured.

| Method | Path | What |
|---|---|---|
| GET | `/api/v1/requirements?status=&release=&label=` | stored requirements with linked case counts |
| GET | `/api/v1/requirements/{id}` | one requirement |
| GET | `/api/v1/requirements/syncs/latest` | the last sync's diff and orphaned links |

Triggering a sync and editing requirements over REST wait for the platform's writer
roles; outside tools own their records, the repository YAML is edited by pull request.

## Configuration

| Option / variable | Default | Meaning |
|---|---|---|
| `--source` | `local` | sources in precedence order |
| `--repo` / `GITHUB_REPOSITORY` | — | issues repository |
| `--label` | `requirement` | label that marks a requirement issue |
| `GITHUB_TOKEN` | — | read token (private repositories) |
| `--snapshot` | `requirements/snapshot.json` | snapshot path |

## Honest limits

A sync checks that ids exist, not that a requirement is testable or that its tests still
match reworded text: a content change only flags the linked tests for a re-review.
