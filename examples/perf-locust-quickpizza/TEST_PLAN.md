# Test plan: QuickPizza Locust

## Scope

- **In scope:** menu reads and the pizza recommendation, under smoke and load.
- **Out of scope:** ratings, users, admin pages, soak. Load against the public instance is
  out by rule: it is shared and offered for small tests only.

## Risks and priority

| Risk | Likelihood | Impact | Priority | Covered by |
|---|---|---|---|---|
| Recommendations slow down under expected traffic | M | H | P1 | `make load` + SLO check |
| Errors appear under expected traffic | M | H | P1 | `make load` + SLO check |
| The SLO check passes a breached run | L | H | P1 | `tests/unit/test_slo.py` |
| A recommendation breaks the customer's restriction | L | M | P2 | `check_recommendation`, `tests/unit/test_users.py` |
| The service is down | L | H | P2 | `make smoke` |

## Automated vs exploratory

- **Automated:** smoke and load with their SLOs; the SLO checker, traffic mix, load shape
  and target guard offline.
- **Exploratory:** where client-side time goes (the k6 comparison), and the stress limit.

## Entry and exit criteria

- **Entry:** `GET /ready` answers 200 on the target.
- **Exit:** smoke and load meet every SLO in `slo.json`.

## Environments and data

- **Target:** `quickpizza-local:0.15.31` for load; the public instance for smoke only.
- **Data:** three restrictions and three menu paths in `users.py`; QuickPizza keeps no
  state between recommendations, so nothing needs cleaning up.

## Known limits

- Locust and QuickPizza share one machine, and Locust's client time is part of every
  latency figure (see the README comparison).
