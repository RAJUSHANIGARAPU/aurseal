| Risk | Profile or test |
|---|---|
| The target is down or a path is broken | `k6/scenarios/smoke.js` (every path in `k6/data/paths.csv`) |
| Latency or errors exceed the SLO at expected peak | `k6/scenarios/load.js` (constant arrival rate) |
| Nobody knows where the capacity limit is | `k6/scenarios/stress.js` (ramps until the error budget is spent) |
| A budget edit makes a threshold meaningless | `tests/unit/test_slo.py` (offline) |
| A load run reaches a host you do not own | `tests/unit/test_guard.py` (offline) and `k6/lib/guard.js` |
