# QuickPizza Locust

> The QuickPizza load profiles in Locust, held to the same SLOs as the k6 example.

A worked example of the `perf-locust` template: the skeleton is rendered from it unchanged
(the files listed in [.scaffold-manifest](.scaffold-manifest)); the simulated customers and
the SLO budgets are written for [QuickPizza](https://github.com/grafana/quickpizza).
[perf-k6-quickpizza](../perf-k6-quickpizza) runs the same traffic with k6, so the two tools
can be read side by side.

## Run it

```bash
docker compose up -d quickpizza                  # QuickPizza on localhost:3333
make load APP_TARGET_URL=http://localhost:3333   # 50 customers for 2 min, then the SLO check
```

Prerequisites: [uv](https://docs.astral.sh/uv/), `make` and Docker. Or, with only Docker:
`make docker-load PROFILE=load` (QuickPizza, one Locust master and two workers, all local).

`make smoke` runs 5 customers for 30 s against the public instance; `make load` and
`make stress` refuse it, because the QuickPizza README offers it for "small-scale
performance tests" only. `make test-offline` runs the SLO checker, user and load-shape
tests with no network.

## Results on a laptop

One run of the load profile against `quickpizza-local:0.15.31` in Docker, Locust 2.46.7 on
the host, 2026-10-18. Numbers from one machine: compare runs with each other, not with
production.

| Endpoint | Requests | Rate | Failed | p50 | p95 | p99 |
|---|---|---|---|---|---|---|
| menu reads (`/`, quotes, names) | 4 710 | 39.5/s | 0 % | 12-14 ms | 22-24 ms | 26-28 ms |
| `POST /api/pizza` (recommend) | 1 190 | 10.0/s | 0 % | 100 ms | 160 ms | 200 ms |
| all | 5 900 | 49.5/s | 0 % | 15 ms | 130 ms | 160 ms |

The SLO check passed: p95 130 ms against a 200 ms budget, no errors. It read 5 850
requests from `reports/load_stats.csv`, 50 fewer than Locust's final console table; the CSV
is written slightly before the last requests finish _(inference; not traced)_.

**Next to k6** (same rates, same machine, see the k6 README): as measured by Locust,
recommendations are about 1.5 times slower (p95 160 ms against 106 ms) and menu reads
about 15 times slower (p50 12 ms against 0.8 ms). QuickPizza did not change; the client
did. Locust's Python HTTP client on the host adds its own time to every request,
while k6 ran in a container next to QuickPizza. Read latency figures as "QuickPizza plus
this client", and compare a tool only with itself.

## Decisions and trade-offs

| Decision | Alternatives considered | Why | Cost accepted |
|---|---|---|---|
| Post-run SLO check on Locust's CSV | Locust's exit code | Locust exits non-zero on any failed request but knows nothing about latency | One more step after each run |
| `constant_throughput(1)` per customer | A fixed 1-3 s pause | Pacing holds the request rate when QuickPizza slows down, the closest Locust gets to k6's arrival rate, so the two examples apply the same load | A customer whose visit takes over 1 s falls behind the rate |
| One request per visit, task sets weighted 4 to 1 | One visit reading every page | Matches the k6 example's 40 reads to 10 recommendations per second | Fewer requests per visit than a real browser |
| Task sets that hand control back (`Visit.leave`) | Plain `TaskSet` | A plain task set keeps the user forever, and the weights would only pick the first visit | One base class to remember |
| Load and stress only against `quickpizza-local` | Load the public instance | The public instance is shared and offered for small tests only | The local container is not production-sized |

## Scope

**In scope:** the menu reads (`/`, `/api/quotes`, `/api/names`) and the pizza
recommendation (`POST /api/pizza`) under three customer restrictions.

**Out of scope**, and the risk each leaves:

- Ratings, users and the admin pages: their latency under load is not measured.
- Soak: a slow leak over hours goes undetected.
- A stress run in this example: the k6 example has one; StepRamp here is unit-tested only.

## What the tests cover

| Risk | Profile or test |
|---|---|
| QuickPizza is down or an endpoint is broken | `make smoke` (profiles/smoke.conf + SLO check) |
| Latency or errors exceed the SLO at 50 requests/s | `make load` |
| The SLO check passes a run it should fail | `tests/unit/test_slo.py` (offline) |
| The mix drifts from 4 reads per recommendation | `tests/unit/test_users.py::test_user_tasks_weighted_to_read_heavy_mix` |
| A recommendation ignores the customer's restriction | `tests/unit/test_users.py` (calories, vegetarian) |
| A load run reaches the public instance | `tests/unit/test_guard.py` (offline) |

## How to read the report

- **Locally:** `reports/<profile>.html` (Locust's report: charts, percentiles, failures)
  and `reports/<profile>_stats.csv`, which the SLO check reads.
- **Verdict:** the SLO check prints `met` or a `BREACHED` line per budget and exits
  non-zero on a breach.
- **A failed run:** the SLO check's output first, then the Failures tab of the HTML report,
  which names the request and what was wrong with the answer.

## Findings

| # | Finding | Severity | Steps to reproduce | Evidence |
|---|---|---|---|---|
| 1 | Measured read latency depends on the load tool more than on QuickPizza at this rate | Info | Run `make load` here and in the k6 example | The comparison above |

## What I would do next

1. Run Locust in a container next to QuickPizza too, so the client side is comparable.
2. A stress run with StepRamp, compared with the k6 stress limit.
3. Trace why the stats CSV trails the console table by about one second of requests.

## Project layout

```text
locustfiles/    entry points: api_user.py (smoke, load), stress.py (with StepRamp)
profiles/       smoke.conf, load.conf, stress.conf: one Locust config per profile
slo.json        the SLO budgets the post-run check enforces
src/perf_locust_quickpizza/
  users.py      QuickPizza customers: menu and order visits, weights, pacing
  shapes.py     StepRamp, the stress profile's load shape
  slo.py        reads Locust's CSV stats and fails on a breached budget
  guard.py      refuses load against a non-local target (make load / stress)
tests/
  unit/         offline tests: SLO check, users, shape, guard, settings
docker-compose.yml  quickpizza-local, a Locust master and workers, all local
```
