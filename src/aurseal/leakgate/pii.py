"""Personal-data rules. They need no private list, so they run everywhere (forks included).

* ``pii.email``  an address outside the reserved/test domains (RFC 2606/6761) and the
  public no-reply addresses;
* ``pii.iban``   an IBAN whose length fits its country and whose mod-97 check is 1;
* ``pii.phone``  an international (``+``) number of 8-15 digits, or a Dutch national
  ``0`` number of exactly 10 digits, not part of a version, hash, timestamp or id;
* ``pii.home``   a home directory path naming a real user (``/Users/<x>/``,
  ``/home/<x>/``, ``C:\\Users\\<x>\\``, ``~<x>/``) or the scanning machine's own ``$HOME``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from pathlib import PurePath

EMAIL = "pii.email"
IBAN = "pii.iban"
PHONE = "pii.phone"
HOME = "pii.home"
RULES = (EMAIL, IBAN, PHONE, HOME)

# Not after a backslash: "\n@pytest.mark" inside a source string is not an address.
_EMAIL_RE = re.compile(
    r"(?<![\w.+\\-])([\w.%+-]+)@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})"
)
_RESERVED_DOMAINS = {"example.com", "example.org", "example.net"}
_RESERVED_SUFFIXES = (".example", ".test", ".invalid", ".localhost")
_PUBLIC_ADDRESSES = {"noreply@github.com"}
_PUBLIC_SUFFIXES = ("@users.noreply.github.com",)

IBAN_LENGTHS = {
    "AD": 24, "AE": 23, "AT": 20, "BE": 16, "BG": 22, "CH": 21, "CY": 28, "CZ": 24,
    "DE": 22, "DK": 18, "EE": 20, "ES": 24, "FI": 18, "FO": 18, "FR": 27, "GB": 22,
    "GI": 23, "GL": 18, "GR": 27, "HR": 21, "HU": 28, "IE": 22, "IS": 26, "IT": 27,
    "LI": 21, "LT": 20, "LU": 20, "LV": 21, "MC": 27, "MT": 31, "NL": 18, "NO": 15,
    "PL": 28, "PT": 25, "RO": 24, "SE": 24, "SI": 19, "SK": 24, "SM": 27, "TR": 26,
}  # fmt: skip
_IBAN_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30})(?![A-Za-z0-9])")

_PHONE_RE = re.compile(
    r"(?<![\w.:#+/=-])"
    r"(\+[1-9]\d{0,3}[ -]?\d(?:[ -]?\d){5,13}|0[1-9]\d{8}|06[ -]\d{8})"
    r"(?![\w]|\.\d|-\d)"
)
_PHONE_SKIP_FILES = ("lock", "package-lock.json", "requirements-lock.txt")

_PLACEHOLDER_USERS = {"runner", "user", "you", "me", "example", "username", "name"}
_HOME_RE = re.compile(
    r"(?:(?<![\w.%-])/Users/|(?<![\w.%-])/home/|(?<![\w])[A-Za-z]:\\{1,2}Users\\{1,2})"
    r"([A-Za-z0-9._$<>{}%-]+)|(?<![\w/])~([a-z][\w.-]*)/"
)


def _email_allowed(local: str, domain: str) -> bool:
    domain = domain.lower()
    address = f"{local}@{domain}".lower()
    if domain in _RESERVED_DOMAINS or domain.endswith(_RESERVED_SUFFIXES):
        return True
    if domain.split(".")[-1] in {"test", "invalid", "localhost", "example"}:
        return True
    return address in _PUBLIC_ADDRESSES or address.endswith(_PUBLIC_SUFFIXES)


def iban_valid(candidate: str) -> bool:
    """Country length and ISO 7064 mod-97 both hold."""
    compact = candidate.replace(" ", "").upper()
    if IBAN_LENGTHS.get(compact[:2]) != len(compact):
        return False
    rearranged = compact[4:] + compact[:4]
    try:
        digits = "".join(str(int(ch, 36)) for ch in rearranged)
    except ValueError:
        return False
    return int(digits) % 97 == 1


def _phone_ok(match: str) -> bool:
    digits = re.sub(r"\D", "", match)
    if match.startswith("+"):
        return 8 <= len(digits) <= 15
    return len(digits) == 10


def _home_users(home: str | None) -> str | None:
    if not home or home in {"/", ""}:
        return None
    if PurePath(home).name.lower() in _PLACEHOLDER_USERS:
        return None
    return home.rstrip("/")


def line_rules(path: str, home: str | None) -> Callable[[str], Iterator[str]]:
    """A function yielding the PII rule ids that one line of ``path`` hits."""
    skip_phone = path.endswith(_PHONE_SKIP_FILES)
    machine_home = _home_users(home)

    def check(line: str) -> Iterator[str]:
        if "@" in line:
            for local, domain in _EMAIL_RE.findall(line):
                if not _email_allowed(local, domain):
                    yield EMAIL
                    break
        for candidate in _IBAN_RE.findall(line):
            if iban_valid(candidate):
                yield IBAN
                break
        if not skip_phone and any(_phone_ok(m) for m in _PHONE_RE.findall(line)):
            yield PHONE
        if _home_hit(line, machine_home):
            yield HOME

    return check


def _home_hit(line: str, machine_home: str | None) -> bool:
    if machine_home and machine_home in line:
        return True
    for windows_or_unix, tilde in _HOME_RE.findall(line):
        user = (windows_or_unix or tilde).strip()
        bare = user.strip("{}$<>").lower()
        if not user or user.startswith(("$", "<", "%", "{")) or bare in _PLACEHOLDER_USERS:
            continue
        return True
    return False
