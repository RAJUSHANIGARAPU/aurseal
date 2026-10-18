"""Start a CI run from outside CI: the seam between "run the nightly now" and a CI service.

The platform UI, another framework or a person at a terminal asks for a run with
lanes, a ref and an environment; a :class:`CiProvider` turns that into the CI
service's own trigger. Only GitHub Actions ``workflow_dispatch`` is implemented;
the protocol keeps other providers possible without touching callers.

Dispatch inputs are validated before anything is sent: an unknown lane name is
rejected here instead of producing a run that silently selects nothing.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from typing import Protocol

DEFAULT_API = "https://api.github.com"


class DispatchError(RuntimeError):
    """The CI service refused or failed a dispatch request."""


class CiProvider(Protocol):
    def dispatch(self, workflow: str, ref: str, inputs: Mapping[str, object]) -> str:
        """Start ``workflow`` on ``ref``; return an id for the request."""
        ...


def normalise_inputs(
    inputs: Mapping[str, object], allowed_lanes: Iterable[str] | None = None
) -> dict[str, str]:
    """Dispatch inputs as the strings the CI service accepts; unknown lanes refused.

    ``lanes`` may be a list or a comma-separated string. Booleans become
    ``"true"``/``"false"`` (``workflow_dispatch`` inputs are strings on the wire).
    """
    out: dict[str, str] = {}
    for key, value in inputs.items():
        if value is None:
            continue
        if key == "lanes":
            lanes = (
                [p.strip() for p in value.split(",")]
                if isinstance(value, str)
                else [str(p).strip() for p in value]  # type: ignore[union-attr]
            )
            lanes = [p for p in lanes if p]
            if allowed_lanes is not None:
                unknown = sorted(set(lanes) - set(allowed_lanes))
                if unknown:
                    raise ValueError(f"unknown lane(s) {unknown}; known: {sorted(allowed_lanes)}")
            out[key] = ",".join(lanes)
        elif isinstance(value, bool):
            out[key] = "true" if value else "false"
        else:
            out[key] = str(value)
    return out


Opener = Callable[[urllib.request.Request], object]


class GitHubActionsProvider:
    """``POST /repos/{repo}/actions/workflows/{workflow}/dispatches``; success is HTTP 204."""

    def __init__(
        self,
        repo: str,
        token_env: str = "GITHUB_TOKEN",
        api_url: str = DEFAULT_API,
        opener: Opener | None = None,
        allowed_lanes: Iterable[str] | None = None,
    ) -> None:
        if "/" not in repo:
            raise ValueError(f"repo must be 'owner/name', got {repo!r}")
        self.repo = repo
        self.token_env = token_env
        self.api_url = api_url.rstrip("/")
        self._open = opener or (lambda req: urllib.request.urlopen(req, timeout=30))
        self.allowed_lanes = tuple(allowed_lanes) if allowed_lanes is not None else None

    def build_request(
        self, workflow: str, ref: str, inputs: Mapping[str, object]
    ) -> urllib.request.Request:
        token = os.environ.get(self.token_env)
        if not token:
            raise DispatchError(f"{self.token_env} is not set; cannot dispatch")
        body = {"ref": ref, "inputs": normalise_inputs(inputs, self.allowed_lanes)}
        url = f"{self.api_url}/repos/{self.repo}/actions/workflows/{workflow}/dispatches"
        return urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def dispatch(self, workflow: str, ref: str, inputs: Mapping[str, object]) -> str:
        request = self.build_request(workflow, ref, inputs)
        try:
            response = self._open(request)
        except urllib.error.HTTPError as exc:
            raise DispatchError(f"dispatch refused: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise DispatchError(f"dispatch failed: {exc.reason}") from exc
        status = getattr(response, "status", None) or getattr(response, "code", None)
        if status != 204:
            raise DispatchError(f"dispatch expected HTTP 204, got {status}")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{workflow}@{ref}:{stamp}"
