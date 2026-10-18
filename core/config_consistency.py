"""
Every host an environment talks to belongs to that environment.

An environment entry names more than one endpoint: the site, its API, perhaps a
document service or an identity host. When a client's key is missing it is
tempting — and common — for the client to fall back to a hard-coded host, which
is always the host of whichever environment the author was using that week. The
run then drives the UI of one environment against the backend of another, and
the failures look like product defects.

``check_environments`` turns that into an offline failure. For every environment
it requires that each ``*_url`` / ``*_host`` key shares the host family of
``base_url``, and that every endpoint key any environment defines is defined by
all of them — a missing key is a finding, never a cue to borrow another
environment's value.

The host family is the registrable domain (the last two labels, three under a
two-letter second-level such as ``co.uk``), ``localhost`` for every loopback
address, or an explicit alias set when one environment legitimately spans two
domains.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Mapping
from urllib.parse import urlsplit

_LOOPBACK_NAMES = {"localhost", "localhost.localdomain"}
_SHORT_SLDS = {"co", "com", "net", "org", "gov", "ac", "edu"}


def is_endpoint_key(key: str) -> bool:
    return key.endswith("_url") or key.endswith("_host")


def host_of(value: str) -> str:
    """The lower-cased host of a URL, or of a bare ``host[:port]`` value."""
    text = value.strip()
    if "://" not in text:
        text = "//" + text
    return (urlsplit(text).hostname or "").lower()


def host_family(value: str, aliases: Mapping[str, str] | None = None) -> str:
    """The family ``value``'s host belongs to (see the module docstring)."""
    host = host_of(value)
    if not host:
        return ""
    if host in _LOOPBACK_NAMES or host.endswith(".localhost"):
        return "localhost"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        return "localhost" if address.is_loopback else host
    labels = host.split(".")
    keep = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SHORT_SLDS else 2
    family = ".".join(labels[-keep:])
    return (aliases or {}).get(family, family)


def same_host_family(a: str, b: str, aliases: Mapping[str, str] | None = None) -> bool:
    """True when both values resolve to the same, non-empty host family."""
    family_a = host_family(a, aliases)
    return bool(family_a) and family_a == host_family(b, aliases)


def check_environments(
    environments: Mapping[str, Mapping[str, object]],
    aliases: Mapping[str, str] | None = None,
    required: Iterable[str] = (),
) -> list[str]:
    """
    Every inconsistency across ``environments``, one human-readable line each.

    ``required`` names endpoint keys every environment must have even when none
    defines them yet; the union of endpoint keys actually present is always
    required. ``aliases`` maps a family onto another (``{"cdn.example": "example.test"}``).
    An empty list means consistent.
    """
    problems: list[str] = []
    expected = set(required)
    for entry in environments.values():
        expected.update(key for key in entry if is_endpoint_key(key))
    expected.add("base_url")

    for env, entry in sorted(environments.items()):
        base = entry.get("base_url")
        if not isinstance(base, str) or not host_of(base):
            problems.append(f"{env}: base_url is missing or has no host")
            continue
        for key in sorted(expected - {"base_url"}):
            value = entry.get(key)
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{env}: {key} is missing (it must not fall back to another env)")
            elif not same_host_family(base, value, aliases):
                problems.append(
                    f"{env}: {key} host {host_of(value)!r} is not in the family of "
                    f"base_url host {host_of(base)!r}"
                )
    return problems
