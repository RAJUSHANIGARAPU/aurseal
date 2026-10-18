"""Generating messages answers 202 with a job id and is followed by polling to DONE."""

from __future__ import annotations

import pytest

from tests.demo_examples.ui import support
from ui.pages.demo.subscription_detail import SubscriptionDetail

pytestmark = pytest.mark.demo_ui


def test_generate_runs_as_polled_job(demo_page, demo_sut, demo_control, demo_seed):
    demo_control.freeze(support.NOW)
    sub = support.booked(demo_seed)
    detail = SubscriptionDetail(demo_page, demo_sut.base_url).open(sub["id"])
    assert [m["status"] for m in detail.message_rows()] == ["NEW"]

    started, polls = detail.generate_messages()

    assert started.status == 202, started.describe()
    job_id = started.body_json()["job_id"]
    assert polls, "the page never polled the job"
    assert polls[0].endswith(f"/api/jobs/{job_id}"), polls[0]
    assert all(url == polls[0] for url in polls), "polled a different job"
    job = demo_sut.client.ok("GET", f"/api/jobs/{job_id}")
    assert job["state"] == "DONE" and job["kind"] == "GENERATE_MESSAGES"
    assert [m["status"] for m in detail.message_rows()] == ["TO_BE_SENT"]
