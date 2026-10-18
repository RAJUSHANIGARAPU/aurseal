"""One id per run, shared by the controller, every xdist worker and every rerun.

The id is what lets evidence written by different processes be judged as one
run: verdict rows, fixture provenance rows, the circuit-breaker trip file and
the run-trust marker all carry it.

Who sets it:

* CI should set ``TEST_RUN_ID`` itself, to something stable across the
  invocations that make up one run — e.g. ``${{ github.run_id }}-${{
  github.run_attempt }}``. A second ``pytest --lf`` with the same id then
  merges into the first run's verdict instead of replacing it.
* When it is absent, the controller (or a plain non-distributed session)
  resolves one through :mod:`core.run_id` (a CI build id when there is one,
  else a local host-and-time id) in ``pytest_configure`` and exports it, before xdist starts
  any worker, so workers inherit it through the environment. The plugin also
  passes it through ``workerinput`` for executors that do not inherit the
  parent environment.

A locally minted id is unique per invocation (to the second), so two invocations
without a shared ``TEST_RUN_ID`` or CI build id are two runs, and the later
marker replaces the earlier one.
"""

from __future__ import annotations

import os

import pytest

from core import run_id as run_ids

ENV = "TEST_RUN_ID"
WORKERINPUT_KEY = "test_run_id"


def current_run_id() -> str | None:
    value = os.environ.get(ENV, "").strip()
    return value or None


def ensure_run_id(config: pytest.Config | None = None) -> str:
    """Return the run id, generating and exporting one on the controller when absent."""
    worker_input = getattr(config, "workerinput", None) if config is not None else None
    if worker_input is not None and worker_input.get(WORKERINPUT_KEY):
        os.environ[ENV] = worker_input[WORKERINPUT_KEY]
        return worker_input[WORKERINPUT_KEY]
    return run_ids.pin_for_session()
