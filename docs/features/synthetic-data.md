# Synthetic and masked test data

## What it does

`test_data.synthetic` generates records from a **constraint profile** and sends them
to a target to find out whether it accepts what it should and rejects what it should.

- **Profile** (`Profile`, `FieldSpec`): an entity's fields with `kind` (`string`,
  `email`, `integer`, `number`, `boolean`, `choice`, `date`), `required`,
  `min_len`/`max_len`, `pattern`, `choices`, `min`/`max`. Plain JSON
  (`schemas/synthetic-profile.schema.json`, examples in `test_data/profiles/`), or
  derived from an OpenAPI request body with `Profile.from_openapi(spec, operation_id)`.
  `Profile.check(values)` names every broken constraint (`name.max_len`,
  `email.format`, `kind.choices`, ...).
- **Generator** (`SyntheticGenerator(seed)`), three modes:
  - `valid`: inside every constraint;
  - `boundary`: still valid, at the edges: shortest and longest strings, smallest and
    largest numbers, each choice, an omitted optional field, and the named extremes
    (right-to-left override, zero-width space, emoji, right-to-left script, a
    256-character string, 2**53) wherever the profile allows them;
  - `invalid`: exactly one constraint broken per record, labelled with it
    (`broken_constraint`). An extreme the profile forbids for exactly one reason
    lands here with that label.

  Every record is reproducible from `(profile, mode, seed, index)` alone, in any
  process. Each record is checked against the profile before it is returned.
- **Masking** (`mask_record(record, rules, key)`): keyed HMAC-SHA256
  pseudonymisation. Same value + same key gives the same output (joins survive);
  `email`, `digits` and `letters` rules keep the format. Fields without a rule are
  untouched.
- **Probe** (`probe(endpoint, http, records)`): POSTs each record and judges the
  answer: expected accept + 2xx = pass; expected reject + 4xx = pass; an invalid
  record accepted, a valid one rejected, or **any 5xx** = fail; no response =
  unreachable.
- **Plugin** `synthetic-data-edge`: generates all three modes, probes the target,
  writes `ai_generated_tests/synthetic/synthetic_profiles.json` (records and verdicts)
  and one `metric_synthetic_probe` row per record. It no longer writes a test stub.

## How to use

```bash
python -m test_data.synthetic generate --profile test_data/profiles/customer.json \
    --mode boundary --n 20 --seed 7
SYNTHETIC_MASK_KEY=change-me python -m test_data.synthetic mask --rules rules.json < records.jsonl
```

```python
from test_data.synthetic import Profile, SyntheticGenerator, SyntheticParams, probe

profile = Profile.from_json("test_data/profiles/customer.json")
records = SyntheticGenerator(seed=7).generate(profile, 10, mode="invalid")
results = probe(profile.endpoint, http, records)  # http: HttpEngine(base_url)

# As a factory prerequisite: values plus the coordinates that reproduce them
Prerequisite("customer", fixture, params=SyntheticParams(profile, "boundary", 7, 3).as_params())
```

Plugin context: `profile` (path, dict or `Profile`), `target` (base URL), optional
`endpoint`, `seed`, `valid_n`, `http`.

| Plugin outcome | Status |
|---|---|
| dry run | `skip`, nothing written |
| no profile, target or endpoint | `unknown` (no default target is invented) |
| Faker not installed | `unknown` |
| target unreachable for any record | `unknown` |
| any 5xx, invalid accepted, valid rejected | `fail` |
| every answer matches its label | `pass` |

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SYNTHETIC_MASK_KEY` | none (required for masking) | HMAC key; never generated silently |
| `METRICS_SINK` / `METRICS_DIR` | `jsonl` / `reports/metrics` | Where probe rows go |

## Example

Against the demo service, the customer profile finds two real gaps: a numeric
`name` is stored (`name.type`, invalid record accepted), and a numeric `email`
crashes validation (`email.type`, server error 500). Everything valid, including the
right-to-left, zero-width and 256-character names, is accepted.

## Honest limits

- HMAC masking is pseudonymisation for test fixtures, not anonymisation: joined with
  outside data, masked records can be re-identifiable.
- `from_openapi` covers JSON-Schema basics only: no `oneOf`/`anyOf`/`allOf`, and
  object or array properties are not profiled.
- The regex sampler supports common pattern syntax (classes, groups, alternation,
  repeats); it refuses look-arounds, negated classes and word boundaries instead of
  guessing.
- Cross-field rules ("email required when contact is by email") are not expressible
  in a profile.
