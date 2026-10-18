---
description: "Use to generate a spec, a page object and a pytest module from a workflow (state-machine) YAML file, then run the generated test until it passes."
disable-model-invocation: true
---

# generate-from-workflow

Turn a workflow definition (the neutral YAML read by `generators/workflow_model.py`) into a
Markdown spec, a page object and a pytest module.

## Usage

```
/generate-from-workflow <path/to/workflow.yaml>
```

## Steps

1. Check the definition hangs together and look at its graph:

   ```bash
   python -m generators.cli graph "$WORKFLOW"
   ```

   Exit code 1 means `problems()` found a transition to an undeclared state or an undeclared
   action: fix the YAML first. Unreachable states are listed; they are findings, not noise.

2. Write the spec and review it:

   ```bash
   python -m generators.cli spec "$WORKFLOW" --out specs
   ```

3. Generate the page object and the test (needs `ANTHROPIC_API_KEY`; bounded by
   `GENERATOR_TIMEOUT_S`, default 420 s):

   ```bash
   python -m generators.cli generate "$WORKFLOW"
   ```

   On any failure nothing is written; existing files stay as they were.

4. Run the generated test two or three times (`pytest ui/tests/test_<key>_generated.py -n 0`),
   fix what fails, and only then commit the spec, page object and test together.

For a `playwright codegen` recording instead of a workflow file use
`python -m generators.cli convert <recording> --name <name>`.
