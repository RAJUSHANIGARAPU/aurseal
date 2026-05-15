# detect-coverage-gaps

Compare what the OpenAPI/Swagger spec (or existing spec files) defines against what the
existing pytest test suite actually covers. Reports untested endpoints and optionally generates
tests to close the gaps.

## Usage

```
/detect-coverage-gaps [specFile | url | all]
```

Examples:
- `/detect-coverage-gaps` — scan all specs and swagger files
- `/detect-coverage-gaps api/postman/sample_collection.json` — one collection
- `/detect-coverage-gaps http://localhost:8080/openapi.json` — live spec

---

## Phase 1 — Inventory specs and OpenAPI sources

```bash
# Find all spec files
find specs/ -name "*.md" 2>/dev/null | sort

# Find any local OpenAPI/Swagger files
find . -name "openapi*.json" -o -name "swagger*.json" 2>/dev/null | grep -v __pycache__ | head -20

# Find Postman collections
find . -name "*.postman_collection.json" -o -name "*.json" -path "*/postman/*" 2>/dev/null | head -10

echo "Spec files: $(find specs/ -name '*.md' 2>/dev/null | wc -l)"
```

---

## Phase 2 — Inventory existing tests

```bash
# All test files
find ui/tests api/tests -name "test_*.py" 2>/dev/null | sort

# What endpoints are already tested
grep -rh "def test_\|client\.\(get\|post\|put\|patch\|delete\)" \
  api/tests/ 2>/dev/null | grep -o '"[^"]*"' | sort -u

# What pages/flows are covered
find ui/tests -name "test_*.py" | xargs grep -h "@pytest.mark\.\|def test_" 2>/dev/null | \
  grep "def test_" | sed 's/def test_//' | sed 's/(.*//' | sort
```

---

## Phase 3 — Parse OpenAPI spec (if available)

```bash
python3 -c "
import json, sys, glob

specs = glob.glob('**/*.json', recursive=True)
specs = [s for s in specs if 'openapi' in s.lower() or 'swagger' in s.lower()]

for path in specs[:3]:
    try:
        with open(path) as f:
            spec = json.load(f)
        if 'paths' not in spec:
            continue
        paths = spec.get('paths', {})
        print(f'{path}: {len(paths)} endpoints')
        for p, methods in list(paths.items())[:5]:
            for method in methods:
                if method in ('get','post','put','patch','delete'):
                    op = methods[method]
                    tags = op.get('tags', ['untagged'])
                    print(f'  [{method.upper()}] {p}  tags={tags}')
    except Exception as e:
        print(f'{path}: ERROR {e}')
"
```

---

## Phase 4 — Parse spec markdown files

For each `specs/*.md` file, read:
- `feature:` frontmatter → feature name
- `## Scenario:` headings → scenario names
- `### Tags` → `ui`, `api`, `smoke`, `regression`

```python
import glob, re

for path in glob.glob('specs/*.md'):
    content = open(path).read()
    feature = re.search(r'feature:\s*(\S+)', content)
    scenarios = re.findall(r'^## Scenario:\s*(.+)$', content, re.MULTILINE)
    tags_blocks = re.findall(r'### Tags\n`([^`]+)`', content)
    print(f'{path}: {len(scenarios)} scenarios  feature={feature.group(1) if feature else "?"}')
    for s in scenarios:
        print(f'  - {s}')
```

---

## Phase 5 — Cross-reference specs vs tests

For each spec scenario:
1. Derive expected test function name: `test_<snake_case_scenario_name>`
2. Search in `ui/tests/` and `api/tests/` for that function
3. Flag as covered or missing

```bash
python3 -c "
import glob, re, os

# Collect all test function names
test_funcs = set()
for path in glob.glob('ui/tests/test_*.py') + glob.glob('api/tests/test_*.py'):
    for line in open(path):
        m = re.match(r'\s*def (test_\w+)', line)
        if m:
            test_funcs.add(m.group(1).lower())

# Check each spec
for spec_path in glob.glob('specs/*.md'):
    content = open(spec_path).read()
    scenarios = re.findall(r'^## Scenario:\s*(.+)$', content, re.MULTILINE)
    for scenario in scenarios:
        key = 'test_' + re.sub(r'[^a-z0-9]+', '_', scenario.lower()).strip('_')
        covered = any(key in f for f in test_funcs)
        print(f'{'✓' if covered else '✗'} {scenario[:60]}  ({os.path.basename(spec_path)})')
"
```

---

## Phase 6 — Generate the gap report

Output:

```
## Coverage Gap Report — <date>

### Summary
| Source | Scenarios/Endpoints | Tests Exist | Covered | Gap |
|--------|---------------------|-------------|---------|-----|
| specs/wiki-search.md | 5 | ✓ | 4/5 | ⚠ 1 missing |
| specs/article-edit.md | 3 | ✗ | 0/3 | ✗ NO TESTS |
| openapi.json | 12 endpoints | partial | 8/12 | ⚠ 4 untested |

### No tests at all (highest priority)
- <specFile> — 0% coverage

### Partially covered
- <specFile>: missing scenarios [<name>, ...]
- openapi.json: untested endpoints [GET /path, POST /path, ...]

### Fully covered
- <specFile>: all scenarios have a matching test function
```

---

## Phase 7 — Optionally generate missing tests

If gaps are found, ask:
> "Found N specs with no tests and M endpoints without coverage.
> Shall I run `/generate-from-prd` for the uncovered specs or `/generate-from-swagger` for the endpoints?"

If yes → invoke the appropriate skill per gap.
If no → save the report only.

```bash
# Save report
mkdir -p target
cat > target/coverage-gap-report-$(date +%Y%m%d).md << 'EOF'
<report content>
EOF
echo "Saved to target/coverage-gap-report-$(date +%Y%m%d).md"
```

---

## Notes

- Run this after every new spec is added or after API changes to catch missing coverage immediately
- Spec `## Scenario:` headings are the unit of coverage — one test function per scenario minimum
- API endpoints with no test → flag with `@pytest.mark.xfail(reason="no test yet")` placeholder
- The wiki_project Postman pipeline (`api/agents/orchestrator.py`) can generate missing API tests automatically
