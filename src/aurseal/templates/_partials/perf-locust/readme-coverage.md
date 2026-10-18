| Risk | Profile or test |
|---|---|
| The target is down or a page is broken | `profiles/smoke.conf` + `make smoke` (SLO check) |
| Latency or errors exceed the SLO at expected peak | `profiles/load.conf` + `make load` |
| Nobody knows where the capacity limit is | `profiles/stress.conf` (`StepRamp` stops once the error budget is spent) |
| The SLO check passes a run it should fail | `tests/unit/test_slo.py` (offline) |
| The traffic mix or the ramp is not what the code says | `tests/unit/test_users.py`, `tests/unit/test_shapes.py` (offline) |
| A load run reaches a host you do not own | `tests/unit/test_guard.py` (offline) |
