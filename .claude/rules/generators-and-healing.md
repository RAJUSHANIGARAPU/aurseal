---
paths:
  - "generators/**"
  - "autonomous_ui/**"
  - "core/ai/**"
  - "plugins/**"
  - "orchestration/**"
---
# Generators and healing: what is live

- `generators` is in the default gate (offline job, `tests/generators`). Generated tests
  carry the `draft` marker and run in the drafts job, never in the gate, until reviewed
  (`python -m traceability review lint`).
- `autonomous_ui`, `plugins` and `orchestration` are local-only: the offline job runs them
  with stubbed transports; a real run needs a browser and a model key.
- The four `core/ai/` modules call the model API directly and need `ANTHROPIC_API_KEY`;
  without it they return `None` or a message, with no CLI fallback.
- `AI_TRANSPORT=none` is what CI uses: no network call, no subprocess.
- Heals are proposed (`*.heals-pending.json`), not written, unless `HEAL_AUTOPERSIST=true`;
  every decision is logged to `reports/healing/healing-events.jsonl`.
- Do not assume a package runs because a document says so: `docs/whats-live.md` is checked
  against the code by `tests/test_docs_drift.py`.
