"""The render lane's target: a render service reached with a supplied bearer token.

``RENDER_BASE_URL`` points at a running service; when unset, the demo service is started
in-process with ``auth_required`` on and the supplied token registered, so the lane runs
end to end without any other setup. No token, or a target that does not answer, abstains:
a skip by default, a failure when ``RENDER_REQUIRED`` is true (the lane's own switch, set
by the render job), so a lane asked to run can never go green having run nothing.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from demo_sut.client import DemoClient
from demo_sut.server import DemoServer
from gate.requirement import Requirement
from gate.token_lane import REQUIRED_ENV, TOKEN_ENV

BASE_URL_ENV = "RENDER_BASE_URL"
RENDER = Requirement(REQUIRED_ENV, "the render lane")


@dataclass(frozen=True)
class RenderTarget:
    base_url: str
    token: str

    def client(self) -> DemoClient:
        return DemoClient(self.base_url)

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


def reachable(base_url: str, timeout_s: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/api/health", timeout=timeout_s):  # noqa: S310
            return True
    except urllib.error.HTTPError:
        return True  # it answered
    except (urllib.error.URLError, OSError, ValueError):
        return False


def external_target(environ=None) -> RenderTarget | None:
    """The configured target, or None to use the in-process demo; abstains on any gap."""
    environ = os.environ if environ is None else environ
    token = (environ.get(TOKEN_ENV) or "").strip()
    if not token:
        RENDER.abstain(f"no bearer token: set {TOKEN_ENV} (auth mode token)")
    base_url = (environ.get(BASE_URL_ENV) or "").strip()
    if not base_url:
        return None
    if not reachable(base_url):
        RENDER.abstain(f"render service {base_url} is unreachable")
    return RenderTarget(base_url, token)


@pytest.fixture(scope="session")
def render_target() -> Iterator[RenderTarget]:
    target = external_target()
    if target is not None:
        yield target
        return
    token = os.environ[TOKEN_ENV].strip()
    with DemoServer() as server:
        server.app.ctx.store.insert(
            "tokens", {"token": token, "subject": "render-lane", "grant": "supplied"}
        )
        DemoClient(server.base_url).settings(auth_required=True)
        yield RenderTarget(server.base_url, token)
