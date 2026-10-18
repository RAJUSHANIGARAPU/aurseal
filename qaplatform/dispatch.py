"""Run dispatch: the platform never executes tests itself; it hands a run request to CI.

A dispatcher gets the stored request and answers ``dispatched`` (CI accepted it) or
``rejected`` (CI refused it, or the request names no known target). The triggered job then
pushes its results like any other run, passing the request id as ``run_request_id`` when it
creates the run; that link, not polling CI, moves the request to ``running`` and, when the
run finishes, ``finished``.

Adapters:

* :class:`NullDispatcher` — nothing configured: the request stays ``queued`` and says so.
* :class:`GitHubWorkflowDispatcher` — GitHub Actions ``workflow_dispatch``: one POST per
  request with the workflow's inputs (``run_request_id`` always included). Targets map to
  workflow files, so only listed targets can be triggered.

Configuration (environment, read by :func:`from_env`): ``QAP_DISPATCHER`` (``github`` or
unset), ``QAP_GITHUB_REPOSITORY`` (``owner/name``), ``QAP_GITHUB_REF`` (default ``main``),
``QAP_GITHUB_TARGETS`` (``target=workflow.yml,...``), ``QAP_GITHUB_TOKEN`` (a token allowed to
dispatch workflows; never logged), ``QAP_GITHUB_API_URL`` (default the public API).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

DEFAULT_GITHUB_API = "https://api.github.com"


@dataclass(frozen=True)
class DispatchResult:
    status: str  # "dispatched" | "rejected" | "queued"
    detail: str = ""
    external_ref: str | None = None


class RunDispatcher(Protocol):
    name: str

    def dispatch(self, request: Mapping[str, Any]) -> DispatchResult: ...


class NullDispatcher:
    name = "none"

    def dispatch(self, request: Mapping[str, Any]) -> DispatchResult:
        return DispatchResult("queued", "no dispatcher is configured on this server")


class GitHubWorkflowDispatcher:
    """``POST /repos/{repo}/actions/workflows/{workflow}/dispatches``."""

    name = "github"

    def __init__(
        self,
        repository: str,
        targets: Mapping[str, str],
        token: str,
        *,
        ref: str = "main",
        api_url: str = DEFAULT_GITHUB_API,
        session: Any = None,
        timeout_s: float = 15.0,
    ) -> None:
        if "/" not in repository:
            raise ValueError("repository must be owner/name")
        self.repository = repository
        self.targets = dict(targets)
        self._token = token
        self.ref = ref
        self.api_url = api_url.rstrip("/")
        self.timeout_s = timeout_s
        if session is None:
            import requests

            session = requests.Session()
        self.session = session

    def inputs(self, request: Mapping[str, Any]) -> dict[str, str]:
        """Workflow inputs: every param as a string, plus the request id, environment, release."""
        out = {str(k): str(v) for k, v in (request.get("params") or {}).items()}
        out["run_request_id"] = str(request["id"])
        for key in ("environment", "release"):
            if request.get(key):
                out[key] = str(request[key])
        return out

    def dispatch(self, request: Mapping[str, Any]) -> DispatchResult:
        workflow = self.targets.get(str(request.get("target")))
        if workflow is None:
            known = ", ".join(sorted(self.targets)) or "none"
            return DispatchResult("rejected", f"unknown target; configured targets: {known}")
        url = f"{self.api_url}/repos/{self.repository}/actions/workflows/{workflow}/dispatches"
        try:
            resp = self.session.post(
                url,
                json={"ref": self.ref, "inputs": self.inputs(request)},
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=self.timeout_s,
            )
        except Exception as exc:  # network failure: the request is not lost, it is rejected
            return DispatchResult("rejected", f"dispatch failed: {type(exc).__name__}")
        if resp.status_code == 204:
            return DispatchResult("dispatched", f"workflow {workflow} on {self.ref}", workflow)
        return DispatchResult("rejected", f"CI answered HTTP {resp.status_code}")


def parse_targets(raw: str) -> dict[str, str]:
    """``"smoke=smoke.yml, regression=tests.yml"`` -> mapping."""
    out = {}
    for part in raw.split(","):
        name, sep, workflow = part.partition("=")
        if sep and name.strip() and workflow.strip():
            out[name.strip()] = workflow.strip()
    return out


def from_env(environ: Mapping[str, str] | None = None) -> RunDispatcher:
    env = os.environ if environ is None else environ
    if env.get("QAP_DISPATCHER", "").strip().lower() != "github":
        return NullDispatcher()
    missing = [k for k in ("QAP_GITHUB_REPOSITORY", "QAP_GITHUB_TOKEN") if not env.get(k)]
    if missing:
        raise ValueError(f"QAP_DISPATCHER=github needs {', '.join(missing)}")
    return GitHubWorkflowDispatcher(
        env["QAP_GITHUB_REPOSITORY"],
        parse_targets(env.get("QAP_GITHUB_TARGETS", "")),
        env["QAP_GITHUB_TOKEN"],
        ref=env.get("QAP_GITHUB_REF") or "main",
        api_url=env.get("QAP_GITHUB_API_URL") or DEFAULT_GITHUB_API,
    )


__all__ = [
    "DispatchResult",
    "GitHubWorkflowDispatcher",
    "NullDispatcher",
    "RunDispatcher",
    "from_env",
    "parse_targets",
]
