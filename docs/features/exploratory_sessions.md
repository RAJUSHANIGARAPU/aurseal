# Exploratory session capture

Session-based exploratory testing recorded next to automated and manual results. The rules
are in `qaplatform/exploratory.py`, the browser capture in `qaplatform/capture.py`; storage in
`qap_sessions` and `qap_session_notes` (migration `0005_exploratory.sql`).

## What it does

- **A session** has a **charter** (the mission), an optional area, linked requirements and
  a timebox; a stream of timestamped **notes** typed `observation`, `question`, `bug` or
  `idea`, each with an optional attachment link and defect key; and a **debrief** (what was
  covered, what was not, follow-up). A session is a run of source `exploratory`.
- **It never proves anything.** Ending a session records `coverage: recorded` for each
  linked requirement, never `proved`: a session is evidence that someone looked, not that a
  requirement holds. In the traceability matrix a requirement touched only by sessions reads
  `recorded_only`, and the session's run is `UNJUDGED` (it executed no tests).
  Abandoning a session records nothing.
- **Timebox:** going over is flagged (`over_timebox`), not refused. Ending twice, or adding a
  note to an ended session, is refused.
- **Bugs:** `bug` notes, with their defect keys, are listed in the session summary; an
  attachment on a note also becomes an evidence link on the session's run.
- **Browser capture:** `open_capture(client, session_id, context)` wraps a Playwright
  browser context the tester drives: it records a trace (screenshots and DOM snapshots) and
  collects console errors, uncaught page errors, failed requests and 5xx responses on every
  page. On close the trace is saved to `reports/sessions/session-<id>.zip` and posted as a
  note attachment, and each collected error becomes a note (20 at most, then one line with
  the remainder). Closing runs on a crash too, so whatever was captured is still posted.
  `capture_in_browser` opens a headed browser on a URL and captures until the window is
  closed. One browser context is recorded; it is not a screen recorder.
- **Audited** (`session.start`, `session.note`, `session.end`); role `tester` or higher.

## How to use

UI: **Sessions** → charter, requirements, timebox → **Start** → add notes as you go →
debrief → **End session**.

CLI (for testers who live in a terminal; needs `QAP_URL` and `QAP_TOKEN`):

```bash
python -m qaplatform session start --charter "Probe refunds with odd amounts" --req REQ-13 --timebox 45
export QAP_SESSION=xs-...
python -m qaplatform session note "negative refund accepted" --defect BUG-77
python -m qaplatform session capture https://staging.example.test/refunds   # headed browser
python -m qaplatform session end --debrief "amounts covered, currencies not"
```

REST: `POST /api/v1/projects/{p}/sessions`, `GET .../sessions?requirement=`,
`GET .../sessions/{id}`, `POST .../sessions/{id}/notes`, `POST .../sessions/{id}/end`.

## Configuration

None. Captures are written under `reports/sessions/` (override with `artifact_dir=`); the
platform stores the path or URL, never the file.

## Example

```python
from playwright.sync_api import sync_playwright
from qaplatform.capture import open_capture
from qaplatform.client import PlatformClient

client = PlatformClient(url, token, "default")
session = client.start_session("Explore checkout with expired cards", requirements=["REQ-21"])
with sync_playwright() as pw:
    context = pw.chromium.launch(headless=False).new_context()
    with open_capture(client, session["id"], context):
        page = context.new_page()
        page.goto("https://staging.example.test/checkout")
        page.wait_for_event("close", timeout=0)      # explore, then close the window
client.end_session(session["id"], "expired cards rejected correctly; retries not tried")
```

## Honest limits

Low leverage for an automation-first suite: it exists so exploratory work is visible next to
automated proof, not to replace a dedicated session tool. Typing into forms is friction the
CLI and capture reduce but do not remove.
