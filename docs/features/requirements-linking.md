# Requirements linking

Tests name the product requirements they cover with a marker; the run writes those links
to a file, and the store and the traceability matrix read them from there. The code
lives in `traceability/requirements.py`, `traceability/links.py` and
`traceability/pytest_plugin.py`.

Not to be confused with `gate/requirement.py`, which decides whether a *suite* or a
*verdict* is required in a lane. This page is about product requirements.

## The registry

**What it does.** `requirements/requirements.yaml` lists every requirement the suite may
link to. It is reviewed like code: add, retitle or retire a requirement in a pull request.

```yaml
requirements:
  - id: REQ-4
    title: Every invoice total equals the sum of its lines
    priority: must        # must | should | could (the release verdict reads it)
    status: active        # active | retired
    labels: [billing]
    release: "1.2"        # optional
```

`Registry.load()` validates the file against
`traceability/schemas/requirements.schema.json` and refuses a duplicate id.
`registry.status("REQ-4")` answers `active`, `retired` or `unknown`.

## The marker

**What it does.** `@pytest.mark.requirement("REQ-4", "REQ-5")` on a function, a class or
a module (`pytestmark`). Ids from every level are merged and sorted.

```python
pytestmark = pytest.mark.requirement("REQ-2")

@pytest.mark.requirement("REQ-3")
def test_cancellation_refunds_the_unused_days():
    ...
```

Every id is checked twice:

1. **Shape** — it matches `REQUIREMENT_ID_PATTERN` and its prefix is a requirement prefix
   (`REQUIREMENT_ID_PREFIXES`). `BUG-142` is refused as a requirement; `REQ-12` is refused
   as a defect id by `known_defect` and `still_reproduces`, so the two namespaces never mix.
2. **Registry** — the id exists and is not retired.

A bad id is a pytest warning. With `REQUIREMENTS_STRICT=true` the test fails at setup with
the id and the reason, so a typo cannot point at nothing. (A setup failure rather than a
collection error: under xdist only workers collect, and a failed test reports the same
way with or without xdist.)

A link is a claim by the test author, not proof. Only a `proved` coverage verdict that
names the requirement upgrades it (see the traceability matrix).

## Coverage verdicts as links

`EvidenceVerdict("REQ-12 renewal date")` or `EvidenceVerdict("renewal", ticket="REQ-12")`
links the test to REQ-12 with origin `coverage_verdict`, so a coverage test needs no
separate marker. A defect ticket on a coverage verdict is not a requirement link. When a
test has markers and its verdict names different ids, the run lists a **mismatch**; the
two are never merged silently.

## Where the links go

| Output | Content |
|---|---|
| `item.user_properties` | `requirements = "REQ-3,REQ-4"` (reaches JUnit XML) |
| Allure | a link of type `requirement` per id |
| `reports/traceability/case-links.json` | written once by the controller, xdist-safe |
| results store | `case_links` rows (kind `requirement`) and `trace_requirements` |

`case-links.json` (schema `traceability/schemas/case-links.schema.json`):

```json
{"schema_version": 1, "run_id": "1234-1", "generated_at": "...",
 "links": [{"nodeid": "tests/test_x.py::test_a", "case_id": "c-1f2e...",
            "requirements": ["REQ-3"], "origin": "marker"}],
 "invalid": [{"nodeid": "tests/test_x.py::test_b", "id": "REQ-404", "problem": "unknown"}],
 "mismatches": [], "tests": {}}
```

A lane other than the default writes `case-links-<lane>.json` (the `RUN_TRUST_LANE`
suffix rule). Other frameworks send `requirements: [...]` with each pushed result, or set
a case's links over REST.

## Command line and REST

```bash
# Collect-only check of every marker id (exit 1 in strict mode when any is bad)
REQUIREMENTS_STRICT=true python -m traceability links --check

# Show the links a run wrote, or store them (plus the registry) in the results store
python -m traceability links --reports reports/traceability
python -m traceability links --reports reports/traceability --db reports/results/results.db
```

| Method | Path | What |
|---|---|---|
| GET | `/api/v1/cases/{case_id}/requirements` | links of one case |
| PUT | `/api/v1/cases/{case_id}/requirements` | `{"requirements": [...], "origin": "external"}` replaces the externally set links |

The handlers live in `traceability/api.py`; importing it adds them to
`results.api.ROUTES`.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `REQUIREMENTS_FILE` | `requirements/requirements.yaml` | registry to validate against |
| `REQUIREMENTS_STRICT` | `false` | bad ids fail the test instead of warning |
| `REQUIREMENT_ID_PATTERN` | `^[A-Z][A-Z0-9]*-\d+$` | id shape |
| `REQUIREMENT_ID_PREFIXES` | `REQ` | prefixes reserved for requirements (comma separated) |
| `REQUIREMENT_URL_PATTERN` | — | e.g. `https://tracker.example.test/{}` for the Allure link |
| `TRACEABILITY_DIR` | `reports/traceability` | where `case-links.json` goes |

## Honest limits

Strict mode breaks the build when the registry drifts from the tests: that is the point,
and it will be felt the first week. A link says the author meant the test to cover the
requirement; it cannot grade whether the assertion does.
