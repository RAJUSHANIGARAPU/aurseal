# Load testing

`checks/load/` runs a real load test with [k6](https://k6.io) and turns its result into a
verdict that can fail. The `load-performance` plugin is a thin adapter over it.

## What it does

* A **load profile** says how load is applied (k6 `stages`: ramp to N virtual users over a
  duration) and what must hold (k6 `thresholds`, e.g. `http_req_duration: p(95)<500`).
  A profile without thresholds is refused: k6 only exits non-zero on a crossed threshold, so a
  run with none could only ever pass.
* The k6 script is rendered from the routes and the profile. Every value is JSON-encoded, so a
  route containing quotes cannot break out of its string. No model writes the script.
* k6 runs with `--summary-export`; the summary is parsed into requests, requests/s, p50/p95/p99,
  error rate, peak VUs and the state of each threshold.
* The verdict (`checks._outcome`, shared by every engine):

| What happened | Status |
|---|---|
| every threshold held, at least one request made | `pass` |
| k6 exited 99 (thresholds crossed), or the summary shows a crossed threshold | `fail`, naming each crossed threshold |
| k6 missing, timed out, exited with any other code, no/unreadable summary, zero requests, a threshold the summary does not report | `unknown` |
| no usable routes, no profile, profile without thresholds | `unknown` |
| dry run | `skip` (script rendered, k6 not started) |

* Each measured run writes one `metric_load_run` row (schema
  `schemas/metrics/metric_load_run.schema.json`) so the trend is visible over runs.

Verified against k6 v2.3.0: exit code 99 on crossed thresholds, and the legacy summary marks a
crossed threshold as `true`.

## How to use

```bash
# against a running service
python -m checks.load --target http://127.0.0.1:8765/api/health \
    --profile checks/load/profiles/smoke.json
# exit 0 = held, 1 = crossed, 2 = nothing judged
```

```python
from pathlib import Path
from checks.load import LoadProfile, run_load
from checks._sink import JsonlSink

profile = LoadProfile.from_json("checks/load/profiles/smoke.json")
outcome = run_load(["http://127.0.0.1:8765/api/health"], profile, Path("reports/load"),
                   sink=JsonlSink())
assert outcome.status == "pass", outcome.reason
```

Plugin context: `routes` (list of absolute URLs), `profile` (a `LoadProfile`, a dict or a JSON
path), optional `out_dir`, `timeout_s`, `k6_bin`, `metric_sink` / `record_metrics`, `dry_run`.

## Configuration

| Setting | Default | Meaning |
|---|---|---|
| `K6_BIN` | `k6` on `PATH` | k6 binary to run |
| `METRICS_DIR` | `reports/metrics` | where `JsonlSink` writes `<table>.jsonl` |
| `--timeout` / `timeout_s` | 600 s | a run cut off at the limit is `unknown` |
| `CHECKS_REQUIRE_TOOLS` | unset | in tests: `true` turns "k6 missing" from a skip into a failure |

Profile JSON:

```json
{"name": "smoke",
 "stages": [["5s", 5], ["10s", 5], ["5s", 0]],
 "thresholds": [{"metric": "http_req_duration", "expr": "p(95)<500"},
                {"metric": "http_req_failed", "expr": "rate<0.01"},
                {"metric": "checks", "expr": "rate>0.99"}],
 "think_time_s": 0.1}
```

## Example

`tests/demo_examples/load/test_load_demo.py` runs k6 against the demo service three times:
generous thresholds hold (`pass`), an impossible latency threshold is crossed (`fail`, named),
and with the demo's `api_unavailable` fault switched on the error-rate threshold is crossed
(`fail`, error rate 1.0). Without k6 those tests skip; the offline engine tests in
`tests/checks/load/` use summaries recorded from real k6 runs.

## Honest limits

Numbers from a shared CI runner against a co-located demo measure the demo on a noisy VM, not
production capacity. Thresholds need one or two calibration runs; a threshold set to "whatever
it did today" is a trend check, not a service-level objective. The trend in `metric_load_run`,
not one run, is the regression signal.
