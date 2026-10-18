"""Refuse a load or stress run against a host this machine does not own.

A public demo host tolerates smoke-sized traffic at most. Load and stress profiles
therefore run only against a local target, unless ``ALLOW_REMOTE_LOAD=1`` records that
the owner of the remote host agreed. The smoke profile is never refused.

``make load`` and ``make stress`` run this before the load tool starts::

    python -m perf_locust_quickpizza.guard load http://localhost:3333
"""

from __future__ import annotations

import ipaddress
import os
import sys
from collections.abc import Mapping, Sequence
from urllib.parse import urlsplit

PROFILES = ("smoke", "load", "stress")
#: Profiles that put real load on the target.
LOAD_PROFILES = frozenset({"load", "stress"})
#: Names that always mean this machine, or a container runtime's route back to it.
LOCAL_NAMES = frozenset({"localhost", "host.docker.internal"})


def is_local(url: str) -> bool:
    """True when ``url`` points at this machine or a container next to it.

    Loopback addresses, ``localhost`` (and ``*.localhost``), ``host.docker.internal``
    and single-label names such as a Compose service (``http://app:8080``) count as
    local. Everything else, private network addresses included, does not: a shared
    staging server is not yours to load without asking.
    """
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    if host in LOCAL_NAMES or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return "." not in host


def refusal(profile: str, url: str, environ: Mapping[str, str]) -> str | None:
    """Why this run may not start, or ``None`` when it may."""
    if profile not in PROFILES:
        return f"unknown profile {profile!r}; expected one of {', '.join(PROFILES)}"
    if not url.startswith(("http://", "https://")):
        return f"target {url!r} is not an http(s) URL"
    if profile not in LOAD_PROFILES or is_local(url):
        return None
    if environ.get("ALLOW_REMOTE_LOAD") == "1":
        return None
    return (
        f"refusing a {profile} run against {url}: it is not a local target. Run the target "
        "locally, or set ALLOW_REMOTE_LOAD=1 once the owner of that host has agreed."
    )


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("usage: python -m perf_locust_quickpizza.guard PROFILE TARGET_URL", file=sys.stderr)
        return 2
    reason = refusal(args[0], args[1], os.environ if environ is None else environ)
    if reason:
        print(reason, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
