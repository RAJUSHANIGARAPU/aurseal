# Locator healing: store, proposals, event log, AI transport

Modules: `autonomous_ui/locator_store.py`, `autonomous_ui/heal_events.py`,
`autonomous_ui/healer.py`, `api/llm/claude_client.py`. The selector priority the
healer asks for is described in [ui-components.md](ui-components.md#selector-priority-model).

## Locator store with heal persistence

### What it does

`LocatorStore` is the only way the healer writes to a locator registry
(`ui/locators/*.json`). Every page object loads that file at construction, so a
write to it is never scoped to the test that triggered the heal. The store makes
the write safe:

- **Atomic.** The new registry goes to a temp file in the same directory and is
  swapped in with `os.replace`. Another xdist worker reading the file sees the
  old version or the new one, never half of one. The file mode is preserved.
- **Serialised.** Writers on one file take a per-path thread lock and an advisory
  file lock (`fcntl`, where the platform has it; the lock file lives in the
  system temp directory). The registry is re-read inside the lock, so two heals
  of different keys from two processes both land.
- **Heal metadata.** A healed entry carries `last_healed` (UTC timestamp) and
  `healed_from` (the entry it replaced), so a diff shows which entries a heal
  produced and what they were before. The locator renderer ignores both keys.
- **In-memory view.** `store.entries` / `store.get(key)` reflect the heal in both
  modes below.

### How to use it

```python
from autonomous_ui.locator_store import LocatorStore

store = LocatorStore("ui/locators/wiki_locators.json")      # mode from HEAL_AUTOPERSIST
write = store.update("search_input", {"type": "role", "role": "searchbox"}, reason="test id removed")
write.persisted     # False by default: a proposal was filed
store.pending()     # {"search_input": {"current": ..., "proposed": ..., "reason": ..., "proposed_at": ...}}
store.apply_pending(["search_input"])   # after review: move it into the registry
```

`apply_pending` skips a proposal whose `current` no longer matches the registry:
somebody changed the entry since, and the proposal was made against something
that is gone.

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `HEAL_AUTOPERSIST` | `false` | `true` writes heals straight into the registry |

## Propose, don't auto-persist

### What it does

By default a heal is **proposed**: it is written to
`<registry name>.heals-pending.json` next to the registry (for example
`ui/locators/wiki_locators.heals-pending.json`) and the committed registry stays
byte-for-byte unchanged. An automatic heal that rewrites a committed file is a
change a review should see before any run depends on it.

The healer reports a proposal honestly: `strategy="locator_patch"`,
`applied=False` (nothing a rerun would pick up), `patched_files` = the side file,
and details that say the registry is unchanged and how to make heals persist.
With `HEAL_AUTOPERSIST=true` (or `UIHealer(autopersist=True)`) the registry is
rewritten through the store and the result is `applied=True` as before.

A suggestion that equals the current entry (heal metadata ignored) is still
refused as a no-op, so a healed entry echoed back by the model is not a second fix.

### Example

```
# Review what the last healing run proposed
cat ui/locators/wiki_locators.heals-pending.json

# Accept all proposals
python -c "from autonomous_ui.locator_store import LocatorStore; \
print(LocatorStore('ui/locators/wiki_locators.json').apply_pending())"

# Let a local healing loop write directly
HEAL_AUTOPERSIST=true python -m autonomous_ui.orchestrator
```

## Heal event log

### What it does

`UIHealer` appends one JSON line per decision — applied, proposed or refused — to
`reports/healing/healing-events.jsonl` through `HealEventLog`. Each line carries
`timestamp`, `test`, `failure_type`, `confidence`, `strategy`, `applied`,
`persisted` (`true` registry written, `false` proposal filed, `null` not a
locator write), `locator_key`, `old_locator`, `new_locator`, `details` and
`patched_files`. Because the healer writes it itself, every caller is covered:
the orchestrator, the healer agent, a script.

The log is bounded like the flakiness history: every 100 appends it keeps the
newest 5000 lines, rewritten through a sibling file and `os.replace`. Writing an
event never fails a heal. A health report that reads this file must report
`NOT MEASURED` (not zero) when there is nothing since the run started.

### How to use it

```python
from autonomous_ui.heal_events import HealEventLog

for event in HealEventLog().read():
    if event["persisted"] is False:
        print("awaiting review:", event["locator_key"], event["new_locator"])
```

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `HEAL_EVENTS_FILE` | `reports/healing/healing-events.jsonl` | where events go |

`HealEventLog(path, max_events=5000, prune_every=100)` sets the bound.

## AI transport selection

### What it does

`ClaudeLLMClient` (used by the healer, the plugins and the orchestration agents)
can be told which transport to use instead of always deciding for itself.
`select_transport` resolves the `transport=` argument, else `AI_TRANSPORT`, else
`auto`:

| Value | Behaviour |
|---|---|
| `auto` | API when `ANTHROPIC_API_KEY` is set, else the `claude -p` CLI; the CLI fallback fires only for a missing key or an unreachable host |
| `api` | API only. No key is `no_credentials`; an unreachable host is `connection`. Never spawns the CLI |
| `cli` | CLI only, even with a key set (a local run on a subscription) |
| `none` | No model at all. Every call returns the named failure `transport_disabled` (and `complete()` returns `""`) without touching the network or spawning anything |

An unknown value raises `ValueError` naming the valid ones: a typo in a CI
variable meant to switch AI off must not switch it on. `effective_transport()`
says what `auto` resolves to right now (API, CLI or none), for a report header.

`AI_CLI_PATH` points at a `claude` executable that is not on `PATH`; when it is
set but not executable the call fails as `cli_missing`.

### How to use it

```python
from api.llm.claude_client import ClaudeLLMClient

client = ClaudeLLMClient()                  # AI_TRANSPORT decides
result = client.complete_result(prompt)
if not result.ok:
    print("no model answer:", result.failure)   # e.g. "transport_disabled: AI_TRANSPORT=none"
```

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `AI_TRANSPORT` | `auto` | `auto`, `api`, `cli` or `none` |
| `ANTHROPIC_API_KEY` | unset | API credential |
| `AI_CLI_PATH` | unset | explicit path to the `claude` CLI |

### Example

```
# CI: AI helpers must never bill or hang the pipeline
AI_TRANSPORT=none pytest tests/ -n auto

# Local: use the logged-in CLI even though a key is exported
AI_TRANSPORT=cli python -m autonomous_ui.orchestrator
```
