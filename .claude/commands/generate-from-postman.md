---
description: "Turn an exported Postman collection (v2.x JSON) into runnable pytest API tests: the API generation pipeline first, a deterministic fallback when it fails. Manual only."
disable-model-invocation: true
---

# generate-from-postman

## Usage

```
/generate-from-postman <path/to/collection.json>
```

## Steps

1. **Pipeline first.** Read the entry points (`main.py`, `api/agents/orchestrator.py`) and
   run the orchestrator on the collection with the configured LLM client, writing into
   `generated_tests/`:

   ```bash
   python main.py --collection "$COLLECTION" --output-dir generated_tests
   ```

   Print whether it succeeded, the pass/fail counts and the report path.

2. **Fallback** when the pipeline fails or writes nothing:

   ```bash
   python -m tools.slash.postman_plan summary "$COLLECTION"
   python -m tools.slash.postman_plan write "$COLLECTION" --out generated_tests
   ```

   - `summary` prints the collection name, the number of top-level items and the first
     requests (name, method, URL);
   - `write` creates one test module per top-level folder with one test function per
     request, in the style of `api/tests/`, under the `api` marker. Calls go through a client
     class: an existing `api/clients/<folder>_client.py` is reused when it has every method,
     otherwise a client is generated next to the tests;
   - collection variables become lookups in the environment configuration
     (`config.get_str(...)`);
   - the collection's test scripts become assertions with the same meaning: status code,
     JSON content type, field equality, array length, header presence. Anything else is
     left as a review note, never guessed.

   Credentials are never copied: an `auth` block or a secret-looking header, query
   parameter, body field or variable becomes a configuration lookup. Check with
   `git diff --no-index /dev/null generated_tests` that no token or password literal is left.

3. **Reachability.** Stop and report the API as down when this fails:

   ```bash
   python -m tools.slash.reach --timeout 10
   ```

4. **Run and fix** until 3 consecutive passes, at most 5 fix rounds:

   ```bash
   python -m tools.slash.stability --state reports/postman-loop.json reset
   pytest generated_tests -m api --junitxml=reports/junit-postman.xml
   python -m tools.slash.stability --state reports/postman-loop.json record --junit reports/junit-postman.xml
   ```

   Diagnose by symptom:

   | Symptom | Likely cause |
   |---|---|
   | 401 | auth missing (configuration key not set) |
   | 404 | wrong path or base path |
   | connection error | wrong base URL, or the service is down |
   | `KeyError` on a JSON field | schema drift: the response changed shape |
   | 201 where 200 was expected (or back) | the collection's status check is stale |

5. **Commit** only after 3 consecutive passes: the generated tests, any moved tests and the
   clients, with a message naming the collection they came from. If the loop gives up,
   nothing is committed.

## Rules

Generated code lands in `generated_tests/` first for review. API tests keep the `api`
marker.

## Output

The generated files, the run result and the push command.
