---
paths:
  - "tests/demo_examples/documents/**"
  - "tests/render_lane/**"
  - "oracles/template_structure.py"
  - "oracles/pdf_*.py"
---
# Document and template checks

Adding a template case to the render suite:

1. Put the template's samples next to the others in
   `tests/demo_examples/documents/templates/samples/` and describe the template in its
   `*.manifest.json`: `pinned_text`, `must_show`, `money_fields`, `conditional_lines` and
   `absent_from_envs`. A template the manifest does not cover is a failure, not a skip.
2. The complete sample must render clean and show every conditional line; the minimal one
   must show none of them and leave no label dangling (`oracles.document_findings`).
3. Structure first, rendering second: `oracles.template_structure` checks the source reads
   only declared payload paths before anything is rendered.
4. A known issue is an expected-red detector with a positive control, never a skip and never
   a weakened assertion. A fix reports the detector cleared; then unwrap it.
5. Fonts: `oracles.pdf_fonts.assert_fonts(pdf, family)` (every font embedded), paired with
   `produced_by_run` so a stored file from an earlier render is not taken as evidence.
6. Checks that need a bearer token live in `tests/render_lane/` and run only in the token
   lane (`render_check` marker); asked for without a token, the lane refuses.
