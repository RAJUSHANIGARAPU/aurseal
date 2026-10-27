# Contract gate

## What it does

Turns contract checks into a gate that can fail a build.

- **Captured contracts** (`contract_testing.pytest_plugin`, `ENABLE_CONTRACT_TESTING=true`):
  `fail_on_breaking` is now honoured. A breaking diff against the stored contract, or
  a provider response that does not match it, sets the session exit status to 1,
  prints what broke and records a `contract` verdict. The breaking contract is **not
  saved**, so the next run is red again until the change is accepted deliberately.
  An unchanged contract saves nothing (no more patch bump on every run). Provider or
  hybrid mode with no captured interaction is reported as **no contract evidence**.
- **OpenAPI schema diff** (`contract_testing.openapi_diff.diff_openapi`): compares two
  OpenAPI 3 documents for any provider language. Breaking: a path, operation or
  success status removed; a response property removed, retyped or made optional; a
  response enum widened; a new required request property or parameter; an optional
  one made required; a request enum narrowed; a body made required. Additive changes
  are non-breaking.
- **Gate** (`contract_testing.gate`): `check_openapi(old, new)` and
  `check_contracts(store, consumer, provider)` (two newest stored versions) return a
  `GateResult` with verdict `compatible`, `breaking` or `no_evidence`. Each judged
  result records a verdict line and `metric_contract_change` rows.
- **Pact export** (`contract_testing.pact_export`): a stored contract as a Pact v3 file
  (example bodies plus `type` matching rules), for teams on Pact tooling. One-way; no
  broker.
- **`api-contract` plugin**: runs the gate on the inputs it is given (`old_spec` +
  `new_spec`, or `consumer` + `provider`). It no longer runs the contract library's own
  unit tests as a stand-in. Breaking is `fail`, compatible `pass`, no inputs or nothing
  to compare `unknown`.

## How to use

```bash
# Exit 0 compatible, 1 breaking, 2 no evidence
python -m contract_testing.gate openapi old-spec.json new-spec.json
python -m contract_testing.gate store --consumer web --provider api --dir contracts
python -m contract_testing.pact_export --consumer web --provider api --out reports/pacts

# Captured contracts, gate on (default)
ENABLE_CONTRACT_TESTING=true CONTRACT_MODE=consumer pytest -m api
# Accept a deliberate breaking change as the new major version
ENABLE_CONTRACT_TESTING=true CONTRACT_FAIL_ON_BREAKING=false pytest -m api
```

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `CONTRACT_FAIL_ON_BREAKING` | `true` | Breaking change or provider mismatch fails the session |
| `CONTRACT_MODE` | `consumer` | `consumer`, `provider` or `hybrid` |
| `CONTRACT_STORAGE_DIR` | `contracts` | Stored contract versions |
| `VERDICTS_FILE` | `reports/verdicts/verdicts.jsonl` | Where the `contract` verdict goes |
| `METRICS_DIR` | `reports/metrics` | Where `metric_contract_change` rows go |

## Example

```text
$ python -m contract_testing.gate openapi demo_sut/openapi.json new-openapi.json
contract gate: BREAKING — 1 breaking, 2 non-breaking
  - [breaking] GET /api/customers/{id} 200.email: response property removed
$ echo $?
1
```

## Consumer fields against the published API document

`contract_testing/consumed_fields.py` needs no older document and no live
call: it reads the API document the deployed service publishes and requires
every field and route its consumers declare they read.

```bash
python -m contract_testing.consumed_fields published-openapi.json consumed.yaml
```

```yaml
schemas:
  Subscription: [id, number, status]
routes:
  - GET /subscriptions/{id}
  - [POST, "/subscriptions/{id}/drafts"]
```

`missing(document, consumed, routes)` returns `Schema.field` and `METHOD path`
entries the document lacks. Schema properties are read through `$ref` and
`allOf`; a path matches once a servlet or gateway prefix is dropped, and path
parameters match whatever they are named. Exit 1 when something is missing.

## Honest limits

- The schema diff only knows what the spec says; an undocumented behaviour change passes.
- The Pact export is a Pact-compatible shape, not Pact provider verification: no
  provider-state handlers, and JSON-Schema formats, numeric ranges and `oneOf` have no
  Pact matcher.
- No contract broker: contracts are files plus the gate. A shared broker is a separate
  decision.
