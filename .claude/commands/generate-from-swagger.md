# generate-from-swagger

Fetch an OpenAPI/Swagger spec (URL or file) and generate pytest API tests covering every
endpoint group — happy path, validation errors, auth boundaries.

## Usage

```
/generate-from-swagger <swagger-url-or-file> [featureName]
```

Examples:
- `/generate-from-swagger http://localhost:8080/openapi.json search`
- `/generate-from-swagger api/postman/sample_collection.json`

---

## Phase 1 — Fetch and parse the spec

```bash
# From URL
curl -s "$SWAGGER_URL" -o /tmp/openapi-spec.json
# Or from file
cp "$SWAGGER_FILE" /tmp/openapi-spec.json

python3 -c "
import json
spec = json.load(open('/tmp/openapi-spec.json'))
print('Title:', spec.get('info',{}).get('title'))
paths = spec.get('paths', {})
print('Endpoints:', len(paths))
for path in list(paths)[:10]: print(' ', path)
"
```

Extract endpoints, tags, schemas, security schemes.

---

## Phase 2 — Plan test coverage

Per tag/path group:
1. **Happy path** — valid request → `2xx`, assert response shape
2. **Missing required field** → `400`
3. **Unauthorized** → `401`
4. **Not found** → `404`

---

## Phase 3 — Generate pytest API test files

One file per OpenAPI tag: `api/tests/test_<tag>_api.py`

```python
import pytest
from api.clients.<tag>_client import <Tag>Client

@pytest.mark.api
@pytest.mark.swagger_generated
class Test<Tag>Api:
    def test_<operationId>_happy_path(self, config):
        client = <Tag>Client(config.get_base_url())
        resp = client.<method>("<path>")
        assert resp.status_code in range(200, 300)

    def test_<operationId>_unauthorized(self, config):
        client = <Tag>Client(config.get_base_url(), token="invalid")
        resp = client.<method>("<path>")
        assert resp.status_code == 401
```

Also generate:
- `api/clients/<tag>_client.py` if the tag doesn't have a client yet
- `ui/testdata/<featureName>-swagger-testdata.json` with example request bodies

---

## Phase 4 — Syntax check

```bash
python3 -m py_compile api/tests/test_*_api.py api/clients/*_client.py
```

---

## Phase 5 — Run → auto-fix loop (3 passes, max 5 iterations)

```bash
source venv/bin/activate 2>/dev/null || true
pytest --env=development -m api api/tests/ -q --tb=short 2>&1 | tail -60
```

**Fix decision tree:**

| Symptom | Fix |
|---------|-----|
| 404 | Path prefix mismatch — check `servers[0].url` |
| 401 on happy path | Auth not set — add `Authorization: Bearer <token>` header |
| `JSONDecodeError` | Response is HTML error page — check if service is up |
| `ConnectionRefusedError` | Base URL wrong or service not started |

---

## Phase 6 — Commit and report

```bash
git add api/tests/ api/clients/ ui/testdata/
git commit -m "feat(api-tests): swagger coverage for <featureName>, passed 3×"
```

---

## Notes

- Run: `pytest -m swagger_generated` to run only this batch
- If spec is behind auth (secured swagger-ui), download the JSON manually first
- Re-run to detect coverage drift after API changes
