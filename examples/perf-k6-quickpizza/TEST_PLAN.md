# Test plan: QuickPizza k6

## Scope

- **In scope:** menu reads and the pizza recommendation, under smoke, load and stress.
- **Out of scope:** ratings, users, admin pages, soak. Load and stress against the public
  instance are out by rule: it is shared and offered for small tests only.

## Risks and priority

| Risk | Likelihood | Impact | Priority | Covered by |
|---|---|---|---|---|
| Recommendations slow down under expected traffic | M | H | P1 | `k6/scenarios/load.js` (recommend p95 threshold) |
| Errors appear under expected traffic | M | H | P1 | `k6/scenarios/load.js` (`http_req_failed`) |
| Capacity limit unknown | H | M | P2 | `k6/scenarios/stress.js` |
| A recommendation breaks the customer's restriction | L | M | P2 | checks in `k6/lib/quickpizza.js` |
| The service is down | L | H | P2 | `k6/scenarios/smoke.js`, nightly |

## Automated vs exploratory

- **Automated:** the three profiles, their SLOs, and the offline checks of the SLO file and
  the target guard.
- **Exploratory:** where the stress errors come from (status codes, timeouts), and whether
  QuickPizza recovers after the ramp stops.

## Entry and exit criteria

- **Entry:** `GET /ready` answers 200 on the target.
- **Exit:** smoke and load meet every SLO; a stress abort is recorded as a finding with the
  rate it reached.

## Environments and data

- **Target:** `quickpizza-local:0.15.31` for load and stress; the public instance for the
  nightly smoke only.
- **Data:** `k6/data/paths.csv` and `k6/data/restrictions.csv`; QuickPizza keeps no state
  between recommendations, so nothing needs cleaning up.

## Known limits

- k6 and QuickPizza share one machine, so the generator competes with the target for CPU;
  the stress limit is a lower bound.
