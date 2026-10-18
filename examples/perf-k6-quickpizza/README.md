# QuickPizza k6

> Smoke, load and stress profiles for the QuickPizza demo API, with SLOs as code.

A worked example of the `perf-k6` template: the skeleton is rendered from it unchanged
(the files listed in [.scaffold-manifest](.scaffold-manifest)); the scenarios, test data and
SLO budgets are written for [QuickPizza](https://github.com/grafana/quickpizza), Grafana's
demo app for k6. [perf-locust-quickpizza](../perf-locust-quickpizza) runs the same traffic
with Locust.

## Run it

```bash
docker compose up -d quickpizza                        # QuickPizza on localhost:3333
docker compose run --rm k6 run k6/scenarios/load.js    # the load profile, in a container
```

Prerequisites: Docker. With [k6](https://grafana.com/docs/k6/latest/set-up/install-k6/),
[uv](https://docs.astral.sh/uv/) and `make` installed locally instead:

```bash
make smoke                                     # 1 VU, 30 s, the public instance
make load APP_TARGET_URL=http://localhost:3333 # 50 requests/s for 1 min, local only
make stress APP_TARGET_URL=http://localhost:3333
```

`make test-local` is what a pull request runs: the smoke profile, k6 in a container,
against QuickPizza in a container next to it, then both are removed. The summaries land in
`reports/local/` and name the target they ran on. Prerequisites: Docker and GNU make.

`make load` and `make stress` refuse the public instance: the QuickPizza README offers it
for "small-scale performance tests" only. `make test-offline` checks the SLO file, the
target guard and the settings without any network.

## Results on a laptop

One run of each profile against `quickpizza-local:0.15.31` in Docker, k6 2.3.0 in a
container next to it, 2026-10-18. Numbers from one machine: compare runs with each other,
not with production.

| Profile | Requests | Rate | Failed | p50 | p95 | p99 | Verdict |
|---|---|---|---|---|---|---|---|
| load (40 reads/s + 10 recommendations/s, 1 min) | 3 002 | 50.0/s | 0 % | 0.8 ms | 89 ms | 106 ms | every SLO met |
| load, recommendations only | 601 | 10.0/s | 0 % | 67 ms | 106 ms | 155 ms | p95 < 200 ms met |
| stress (ramp to 400 recommendations/s) | 5 998 | 111/s | 2.8 % | 68 ms | 1 006 ms | 1 561 ms | aborted after 54 s |

The stress run is the finding. It stopped itself 54 s in, while the target rate for
recommendations was ramping from 100 to 200 per second: 167 recommendations had failed
(menu reads did not fail), 158 iterations could not start for lack of a free VU, and the
1 % error budget was spent. The overall rate actually achieved was 111 requests/s. So on
this machine the recommendation endpoint saturates at roughly 100 requests/s _(inference
from one run; repeat it before quoting the number)_.

## Decisions and trade-offs

| Decision | Alternatives considered | Why | Cost accepted |
|---|---|---|---|
| Thresholds decide pass or fail, checks only diagnose | Fail on any failed check | A threshold is an SLO with a budget; a check says which request broke and why (status, calories, vegetarian) | A failing check alone never fails the run |
| Two arrival-rate scenarios (reads, recommendations) | One VU loop doing both | Each rate is set and reported on its own; the open model keeps requests arriving when QuickPizza slows down | Two rates to keep in proportion |
| A separate p95 threshold on `recommend` | Only the overall p95 | Fast menu reads outnumber recommendations 4 to 1 and would hide a slow recommendation in the overall percentile | One more line in the verdict |
| One token per VU | One shared token | QuickPizza treats each token as a customer; one per VU spreads the load the way real users would | None measured |
| Load and stress only against `quickpizza-local` | Load the public instance | The public instance is shared and offered for small tests only | The local container is not production-sized |

## Scope

**In scope:** the menu reads (`/`, `/api/quotes`, `/api/names`) and the pizza
recommendation (`POST /api/pizza`) under three customer restrictions.

**Out of scope**, and the risk each leaves:

- Ratings, users and the admin pages: their latency under load is not measured.
- Soak: a slow leak over hours goes undetected.
- The browser front end: page rendering time is not measured, only the API behind it.

## What the tests cover

| Risk | Profile or test |
|---|---|
| QuickPizza is down or an endpoint is broken | `k6/scenarios/smoke.js` (every path, every restriction) |
| Latency or errors exceed the SLO at 50 requests/s | `k6/scenarios/load.js` |
| Nobody knows where the recommendation endpoint saturates | `k6/scenarios/stress.js` (stops itself once 1 % of requests fail) |
| A recommendation ignores the customer's restriction | checks in `k6/lib/quickpizza.js` (calories, vegetarian) |
| A budget edit makes a threshold meaningless | `tests/unit/test_slo.py` (offline) |
| A load run reaches the public instance | `tests/unit/test_guard.py` (offline) and `k6/lib/guard.js` |

## How to read the report

- **Locally:** each profile writes `reports/k6-<profile>-summary.html` and
  `reports/k6-<profile>-summary.json`, and prints each SLO as `met` or `BREACHED`.
- **Exit code:** k6 exits non-zero when a threshold is breached (99), and `make` stops there.
- **A failed run:** the SLO table first, then the checks table, which names the request
  that misbehaved.

## Findings

| # | Finding | Severity | Steps to reproduce | Evidence |
|---|---|---|---|---|
| 1 | The recommendation endpoint saturates at roughly 100 requests/s on a laptop; past that, 2.8 % of requests fail and p95 passes 1 s | Info | `make stress APP_TARGET_URL=http://localhost:3333` | The stress row above |

## What I would do next

1. Compare the stress limit across QuickPizza versions, with the image tag as the only change.
2. A soak profile (1 h at the load rate) to look for slow degradation.
3. Send the results to a time-series store, so the trend is visible, not just the verdict.

## Project layout

```text
k6/
  scenarios/      smoke.js, load.js, stress.js: one profile per file
  lib/            config, target guard, checks, test data, QuickPizza calls, summary
  data/           paths.csv (menu reads), restrictions.csv (customers)
  slo.json        the SLO budgets; thresholds.js turns them into k6 thresholds
src/perf_k6_quickpizza/
  guard.py        refuses load against a non-local target (make load / stress)
  slo.py          validates slo.json
tests/
  unit/           offline tests: SLO file, guard, settings
docker-compose.yml  quickpizza-local and k6, both local
```
