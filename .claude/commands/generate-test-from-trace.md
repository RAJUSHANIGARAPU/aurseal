---
description: "Produce a runnable pytest + Playwright test (and optionally a page object) from a recorded Playwright trace archive, using the repository's TestGenerator. Manual only."
disable-model-invocation: true
---

# generate-test-from-trace

## Usage

```
/generate-test-from-trace [path/to/trace.zip] [snake_case_name]
```

Without a path, list the three newest traces and use the newest unless the user picks
another. Without a name, it is derived from the first URL visited in the trace.

## Steps

1. **Resolve the trace:**

   ```bash
   python -m tools.slash.traces list
   ```

   Name the trace that will be used. Stop with a clear message when there is none, or
   when the file is not a readable trace archive - before any file is written.

2. **Generate** the test module (and the page object when the user asks for one, or when
   none exists for this screen) through `core.ai.test_generator.TestGenerator`:

   ```bash
   python -m tools.slash.traces generate --trace "$TRACE" --name "$NAME" [--page-object]
   ```

   It writes `ui/tests/test_<name>.py` and `ui/pages/<name>_page.py` (class name from the
   flow name, the URL pattern the page lives under, a one-line purpose). When a target
   file already exists it stops: **ask the user** and re-run with `--overwrite` only on a
   yes. Without generator credentials (`ANTHROPIC_API_KEY`) it says so and writes nothing;
   never write code by hand in its place. Print the generated code.

3. **Compile** happens before writing; a compile error stops the command before any test
   runs.

4. **Run** the new module verbosely and report the result:

   ```bash
   pytest ui/tests/test_${NAME}.py -v
   ```

## Conventions the generated code must follow

- Page objects extend `BasePage` (`core/base_page.py`).
- UI checks use Playwright's web-first `expect(...)`, not bare asserts.
- Selector order: test id, accessible label, role, visible text.
- No fixed sleeps; wait on the load state or on an element becoming visible with a timeout.

The helper rejects generated code with a sleep or a page object that does not extend
`BasePage`.

## Output

The files written, the compile result, and pass/fail of the run (with the failure summary
when red).

## Stop conditions

No trace found; generator unavailable; compile failure; overwrite declined.
