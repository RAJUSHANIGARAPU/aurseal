"""Platform release smoke: jobs, files and exports answer and read back.

The read half goes through ``oracles.smoke.run_smoke`` over a ``GetOnlyApi`` (a smoke must
never abstain: an unreachable service is a failure). The export half creates one export and
reads it back with its attempt bookkeeping.
"""

from __future__ import annotations

from datetime import datetime

from oracles.data_health import GetOnlyApi
from oracles.smoke import SmokeCheck, describe, json_path_is_list, run_smoke, status_is


def test_jobs_files_exports_read_back(demo_seed, demo_http, demo_control):
    sub = demo_seed.subscription(start_date="2026-03-01")  # creates booking jobs and documents
    demo_seed.book_due(sub["id"], until="2026-03-31")
    api = GetOnlyApi.over(demo_http)

    listing = run_smoke(
        api,
        [
            SmokeCheck("jobs page", "jobs", (status_is(200), json_path_is_list("items", 1))),
            SmokeCheck("files", "files", (status_is(200), json_path_is_list("", 1))),
            SmokeCheck("exports", "exports", (status_is(200), json_path_is_list(""))),
        ],
    )
    assert all(r.ok for r in listing), describe(listing)

    job = demo_http.get("jobs", params={"limit": 1}).json()["items"][0]
    file = demo_http.get("files").json()[-1]
    read_back = run_smoke(
        api,
        [
            SmokeCheck("job by id", f"jobs/{job['id']}", (status_is(200),)),
            SmokeCheck("file by id", f"files/{file['id']}", (status_is(200),)),
        ],
    )
    assert all(r.ok for r in read_back), describe(read_back)
    assert demo_http.get(f"jobs/{job['id']}").json()["id"] == job["id"]
    assert demo_http.get(f"files/{file['id']}").content, "file read back empty"

    created = demo_http.post("exports", json={"kind": "subscriptions"})
    assert created.status == 201, created
    export = demo_http.get(f"exports/{created.json()['id']}").json()
    for record in [*demo_http.get("exports").json(), export]:
        assert record.get("attemptCount") is not None and record.get("lastAttemptAt"), record
    assert export["attemptCount"] >= 1
    assert datetime.fromisoformat(
        export["lastAttemptAt"].replace("Z", "+00:00")
    ) >= datetime.fromisoformat(export["created_at"].replace("Z", "+00:00"))
