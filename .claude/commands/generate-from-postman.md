# generate-from-postman

Convert a Postman collection export (.json) into pytest API tests using the framework's
existing `api/agents/` pipeline (IngestionAgent → GenerationAgent → ExecutionAgent → SelfHealingAgent).

## Usage

```
/generate-from-postman <path/to/collection.json>
```

---

## Phase 1 — Use the existing pipeline

wiki_project has a full Postman ingestion pipeline already. Read it first:

```bash
cat api/agents/ingestion.py | head -60
cat api/agents/generation.py | head -60
cat api/agents/orchestrator.py | head -60
```

Invoke the orchestrator directly:

```bash
source venv/bin/activate 2>/dev/null || true
python3 - <<'EOF'
from api.agents.orchestrator import Orchestrator
from api.llm.claude_client import ClaudeLLMClient

llm = ClaudeLLMClient()
orch = Orchestrator(llm=llm, output_dir="generated_tests")
result = orch.run("$COLLECTION_JSON")
print("Success:", result.success)
print("Generated files:", result.generated_files)
print("Test results:", result.execution_result)
EOF
```

If the orchestrator fails or produces no output, fall through to Phase 2 (manual generation).

---

## Phase 2 — Manual generation (fallback)

If the pipeline fails, parse the collection directly:

```bash
python3 -c "
import json
with open('$COLLECTION_JSON') as f:
    c = json.load(f)
print('Collection:', c['info']['name'])
items = c.get('item', [])
print('Top-level items:', len(items))
for i in items[:5]:
    print(' -', i.get('name','?'), i.get('request',{}).get('method',''), i.get('request',{}).get('url',{}).get('raw','') if isinstance(i.get('request',{}).get('url'), dict) else i.get('request',{}).get('url',''))
"
```

Generate pytest files following the pattern in `api/tests/test_search_api.py`:
- One file per folder/group → `api/tests/test_<folder>_api.py`
- One test function per request
- Use `api/clients/` classes for HTTP calls (create a new client class if the endpoint isn't covered)
- Map Postman `{{variable}}` → `config.get_base_url()` or environment config

**Postman assertion → Python assertion mapping:**

| Postman | Python / pytest |
|---------|----------------|
| `pm.response.to.have.status(200)` | `assert response.status_code == 200` |
| `pm.response.to.be.json` | `assert "application/json" in response.headers.get("content-type","")` |
| `pm.expect(data.field).to.equal(x)` | `assert response.json()["field"] == x` |
| `pm.expect(arr).to.have.length(n)` | `assert len(response.json()) == n` |
| `pm.response.to.have.header("X")` | `assert "X" in response.headers` |

---

## Phase 3 — Run → auto-fix loop (3 passes, max 5 fix iterations)

```bash
BASE_URL=$(python3 -c "import yaml; d=yaml.safe_load(open('config/development.yml')); print(d.get('base_url',''))" 2>/dev/null)
curl -s -o /dev/null -w "%{http_code}" "$BASE_URL" --max-time 10
```

If not reachable: stop, report the API is down.

```bash
pytest --env=development -m api generated_tests/ api/tests/ -q --tb=short 2>&1 | tail -60
```

**Fix decision tree:**

| Symptom | Fix |
|---------|-----|
| `401 Unauthorized` | Auth not configured — add token to config or request headers |
| `404 Not Found` | URL path wrong — re-check the Postman URL |
| `ConnectionError` | Base URL wrong or service not running |
| `KeyError` on JSON field | Response schema changed — update assertion to use `.get("field")` |
| `AssertionError` on status | Check if API returns 200 vs 201 for POST |

---

## Phase 4 — Commit and report

```bash
git add generated_tests/ api/tests/ api/clients/
git commit -m "feat(api-tests): generated from Postman collection $(basename $COLLECTION_JSON), passed 3×"
```

Report generated files and push command.

---

## Notes

- Generated tests land in `generated_tests/` first — review before moving to `api/tests/`
- Postman `{{env_variables}}` map to `config/development.yml` keys
- Strip any hardcoded tokens/passwords from generated code
- Run with `-m api` marker to keep API and UI test runs separate
