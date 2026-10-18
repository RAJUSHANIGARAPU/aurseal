# Platform web UI

A server-rendered UI, read-only except for the manual runner and exploratory sessions, served by the same app as the REST API, under `/ui`. One
Jinja2 template per page, no JavaScript and no build step. The code is
`qaplatform/http/ui/` (`views.py` view models, `pages.py` page handlers, `templates/`).

## What it does

| Page | Path |
|---|---|
| Projects visible to your token | `/ui/` |
| Runs (filter by status) | `/ui/{p}/runs` |
| Run detail: integrity verdict, counts, breaches, results, verdict rows, evidence | `/ui/{p}/runs/{run_id}` |
| Outcome history of one test | `/ui/{p}/history?test_id=...` |
| Requirement traceability matrix | `/ui/{p}/requirements?release=...` |
| Release verdict (or why there is none) | `/ui/{p}/releases/{release}` |
| Audit log with hash-chain status (role `lead`+) | `/ui/{p}/audit` |
| Manual runs: start from a spec, record steps, finish (role `tester`+ for the forms) | `/ui/{p}/manual` |
| Exploratory sessions: charter, notes, debrief (role `tester`+ for the forms) | `/ui/{p}/sessions` |

Rules the pages follow, tested on the view models without a browser:

- **The run-trust verdict is the headline** of every run page; an open run is labelled
  provisional; a run with nothing executed says "nothing executed" and is never described
  as passed.
- **A pass count never stands alone:** it is shown with `proved`, `recorded only`,
  `not exercised` and `abstained`, and with "received / declared", so a green that proved
  nothing cannot look like a green that proved everything.
- **One code path with the API:** pages call the same `PlatformService` reads, with the same
  role and project checks; a viewer sees no form that changes data, another project's
  pages answer 404.
- **Safe output:** everything is HTML-escaped; evidence URIs become links only when they are
  `http(s)`; responses carry a strict `Content-Security-Policy` (no scripts at all),
  `X-Content-Type-Options: nosniff` and `Cache-Control: no-store`.

## How to use

```bash
pip install -r requirements.txt -r requirements-platform.txt
python -m qaplatform serve          # then open http://127.0.0.1:8765/ui/
```

Sign in by pasting an API token (the platform's only identity). It is kept in an
`HttpOnly`, `SameSite=Strict` cookie scoped to `/ui` for 12 hours; forms that change data
carry a CSRF value derived from the token. A viewer token is the right one for someone who
only reads.

## Configuration

None. The UI is mounted whenever Jinja2 is installed; without it the API still runs.

## Example

`GET /ui/default/runs/r-5d1c` for a run that declared 40 tests and reported 8 shows:

```
Run trust: UNJUDGED  (no red among 8 executed)
received / declared 8 / 40 · passed 8 · proved 0 · recorded only 0 · not exercised 0 ...
Why the verdict is not PASS
  SELECTION: 8 of 40 declared test(s) reported a result
```

## Honest limits

Plain on purpose: no charts (trend dashboards are the static dashboard's job), no
client-side sorting. Pages show the first 200 results and verdict rows of a run; the API
pages through the rest.
