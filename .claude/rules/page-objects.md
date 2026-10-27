---
paths:
  - "ui/pages/**"
  - "ui/components/**"
  - "ui/locators/**"
  - "ui/tests/**"
---
# Page objects and selectors

- UI interactions live in `ui/pages/` (and reusable widgets in `ui/components/`), never in
  test files. A test reads as steps and assertions.
- Locators are JSON-managed under `ui/locators/`; do not hardcode a selector in Python.
- Selector priority: `get_by_test_id` > `get_by_role` > `get_by_label` > `get_by_text`.
  Never a CSS class chain or an XPath that encodes layout.
- Wait on state, never on time: `expect(locator).to_be_visible(timeout=...)` or a load state;
  no `time.sleep()`.
- Re-resolve a locator after the page re-renders; never hold an element handle across a
  dynamic update.
- A healed locator is proposed into `ui/locators/<name>.heals-pending.json` by default; it
  becomes the registry only after review (`HEAL_AUTOPERSIST=true` writes directly).
- Read `docs/ai_learnings.md` before writing or fixing a UI test.
