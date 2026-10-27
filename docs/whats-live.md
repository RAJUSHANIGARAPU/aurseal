# What runs where

What actually runs in CI, what runs only in an opt-in lane, what is local-only, and what
nothing exercises. Measured, not remembered: `tests/test_docs_drift.py` checks every row
of both tables against the code (a package marked experimental is imported by no test; any
other is imported by at least one; a marker row agrees with `gate.lanes`).
`tests/test_marker_inventory.py` checks that the marker table lists exactly the markers
`pytest.ini` declares, and that each one is applied by a test or is a lane-only marker.

Status vocabulary:

* **default gate** — exercised on every push to `main` and every pull request by a required
  job of `.github/workflows/tests.yml` (mostly the offline job);
* **opt-in lane** — runs only when its toggle, input or schedule is set (named in the row);
* **local-only** — has tests in the gate, but its real use needs something CI does not have
  (a model key, a browser session, a person);
* **experimental** — no test imports it; it may work, nothing proves it.

Markers have one more status: **metadata** — runs wherever its test runs; the marker only
labels, groups or links the test (what reads it is named in the row).

## Packages

| Package | Status | Where | Notes |
|---|---|---|---|
| `gate` | default gate | offline job (`tests/gate`) | run trust, lanes, workflow lint, CI helpers |
| `core` | default gate | offline job (`tests/core`) | config ladder, auth, logging, artefact policy |
| `api` | default gate | offline job (`tests/api*`), browser job (`api` marker) | HTTP engine and agents |
| `ui` | default gate | browser job (`smoke`, `contract`), demo UI examples | page objects need a browser |
| `oracles` | default gate | offline job (`tests/oracles`, demo examples) | |
| `verification` | default gate | offline job (`tests/verification`) | known defects, verdicts |
| `test_data` | default gate | offline job (`tests/test_data`) | seed modes, cleanup ledger |
| `demo_sut` | default gate | offline job, browser job (demo UI examples) | in-repo demo service |
| `results` | default gate | offline job (`tests/results`), results-history and dashboard jobs | |
| `traceability` | default gate | offline job, browser job (defect drafts, gaps) | |
| `reporting` | default gate | offline job (`tests/reporting`) | Allure metadata |
| `contract_testing` | default gate | offline job (`tests/contract_testing`, contract gate step) | |
| `generators` | default gate | offline job (`tests/generators`) | generated tests carry `draft` |
| `checks` | default gate | checks job (`tests/checks/db`); load checks are opt-in (`checks` lane, nightly) | |
| `qaplatform` | default gate | offline job (`tests/qaplatform`), platform job (`tests/qaplatform_http`) | |
| `tools` | default gate | offline job (`tests/tools`) | leak gate, promotion diff, push guard, rules lint |
| `integrations` | default gate | offline job (`tests/integrations`) | |
| `web_discovery` | default gate | offline job (`tests/web_discovery`) | crawling a real site is local-only |
| `plugins` | local-only | offline job (`tests/plugins`) with stubbed transports | real runs need a model key |
| `orchestration` | local-only | offline job (`tests/orchestration`) with stubbed transports | real runs need a model key |
| `autonomous_ui` | local-only | offline job (`tests/autonomous_ui`) with stubbed transports | healing loop needs a browser and a model |
| `memory` | local-only | offline job (`tests/memory`) | used by local agent runs |
| `scripts` | local-only | one offline test | `auto_runner.py` needs a model key |
| `config` | experimental | — | `config/settings.py` is imported by nothing; `config/environments.json` is read by `core.config_reader` |

Data directories (no code): `defects/`, `schemas/`, `security/`, `specs/`, `plans/`,
`visual_baselines/`, `generated_tests/` (empty until a generator writes a draft).

## Markers and lanes

| Marker | Status | Toggle or job |
|---|---|---|
| `smoke` | default gate | browser job |
| `api` | default gate | browser job |
| `contract` | default gate | browser job |
| `e2e` | default gate | sequential unless `E2E_PARALLEL_SAFE` |
| `stateful` | default gate | sequential unless `E2E_PARALLEL_SAFE` |
| `demo_ui` | default gate | browser job (demo UI examples), visual and platform jobs |
| `regression` | opt-in lane | browser job on `main`, nightly, or `lanes=regression` |
| `slow` | opt-in lane | `RUN_SLOW_TESTS` (nightly, or `lanes=slow`) |
| `on_demand` | opt-in lane | `INCLUDE_MANUAL_CHECKS` |
| `unit` | opt-in lane | own lane, run first |
| `draft` | opt-in lane | drafts job (never gates) |
| `render_check` | opt-in lane | render job (`run_token_lane`, or nightly with `RENDER_TOKEN`) |
| `negative` | metadata | select with `-m negative` |
| `seeded` | metadata | run-trust fixture-provenance rule (`gate.integrity`) |
| `journey_group` | metadata | fail-fast group, `FAILFAST_JOURNEY_GROUP` (`gate.journey_group`) |
| `case_id` | metadata | test case catalogue (`results.catalogue`) |
| `owner` | metadata | test case catalogue (`results.catalogue`) |
| `priority` | metadata | test case catalogue (`results.catalogue`) |
| `feature` | metadata | test case catalogue, Allure labels |
| `requirement` | metadata | requirement links, `REQUIREMENTS_STRICT` (`traceability`) |
| `service` | metadata | service selection map (`gate.service_selection`) |
| `covers` | metadata | coverage gaps (`traceability.gaps`) |
| `generated_batch` | metadata | `/generate-from-swagger` batch selection |

Workflows outside `tests.yml`: the secret scan (every push, `secret-scan.yml`), the mutation
score gate (nightly, `mutation.yml`), the release verdict (tags, `release.yml`), the live
run of the `examples/` projects (nightly, `examples-nightly.yml`; opens an issue instead of
failing, see [ci-decisions](ci-decisions.md#examples-lane)).
