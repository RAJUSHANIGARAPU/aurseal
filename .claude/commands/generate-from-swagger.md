---
description: "Generate pytest API tests from an OpenAPI/Swagger document, one module per tag, covering the success path and the 400/401/404 responses each operation can actually produce. Manual only."
disable-model-invocation: true
---

# generate-from-swagger

## Usage

```
/generate-from-swagger <spec URL or file> [feature]
```

`feature` defaults to a snake_case form of the document title. A document behind
authentication must be downloaded by hand first; pass the local file.

## Steps

1. **Load and look.** Print the title, the number of paths, the first paths and the
   security schemes:

   ```bash
   python -m tools.slash.openapi_plan summary "$SPEC"
   ```

2. **Plan per tag** (done by the helper): a success case for every operation (valid
   request, 2xx, response shape checked) plus only the failure cases it can produce -
   `400` when something required can be left out, `401` when the operation is secured,
   `404` when the path names a resource. A public operation gets no 401 test.

3. **Generate** one module per tag with the `api` marker and the batch marker
   `generated_batch(name="<feature>")`, one test per operation and case, a client per tag
   that has none in `api/clients/` (an existing client is reused, never duplicated), and
   example request bodies in `<feature>_api_data.json`:

   ```bash
   python -m tools.slash.openapi_plan write "$SPEC" --feature "$FEATURE" --out generated_tests
   ```

4. **Compile** the tests and clients: `python -m compileall -q generated_tests/$FEATURE`.

5. **Run and fix** until 3 consecutive passes, at most 5 fix rounds:

   ```bash
   python -m tools.slash.reach --timeout 10 || exit 1
   python -m tools.slash.stability --state reports/swagger-loop.json reset
   pytest generated_tests/$FEATURE -m "generated_batch(name='$FEATURE')" \
     --junitxml=reports/junit-swagger.xml
   python -m tools.slash.stability --state reports/swagger-loop.json record --junit reports/junit-swagger.xml
   ```

   | Symptom | Likely cause |
   |---|---|
   | 404 on every call | the server's base path differs from the document's |
   | 401 on a success case | the auth header is missing (`API_TOKEN` not configured) |
   | JSON decode error | an HTML error page came back: the service is down or misrouted |
   | connection refused | wrong base URL |

6. **Commit** the tests, clients and test data only after 3 consecutive passes. If the
   loop gives up, nothing is committed.

Re-running the command after the API changes shows the coverage drift.
