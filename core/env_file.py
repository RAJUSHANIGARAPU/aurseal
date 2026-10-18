"""
Reading a per-environment ``.env.{env}`` file.

Credentials for a local run (a test user's password, a client secret) must not be
committed, and typing them into every shell is how they end up in a history file.
A git-ignored ``.env.qa`` next to the repository root solves both: the reader
picks it up for ``--env=qa`` and nothing else.

The file is a *rung* in the configuration ladder, not a replacement for the real
environment. ``ConfigReader`` consults it after ``os.environ`` and before the
committed ``environments.json``, so a CI job that exports a variable always wins
over whatever a developer left lying in the checkout.

Only the subset of the dotenv format that real files use is supported: ``KEY=value``,
an optional ``export`` prefix, ``#`` comments, and single- or double-quoted values.
No variable expansion — a value that looks like ``$OTHER`` is taken literally, which
is the safer failure for a password.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.logger import logger

_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}


def env_file_path(env: str, directory: str | Path = ".") -> Path:
    """Where the file for ``env`` lives: ``<directory>/.env.<env>``."""
    return Path(directory) / f".env.{env}"


def parse_env_text(text: str) -> dict[str, str]:
    """
    Parse dotenv text into a dict.

    Malformed lines are skipped with a warning naming the line number, never the
    content: a malformed line in a credentials file may well hold a secret.
    """
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, rest = line.partition("=")
        key = key.strip()
        if not sep or not _KEY.match(key):
            logger.warning("env file: line %d is not KEY=value, skipped", number)
            continue
        values[key] = _value(rest.strip())
    return values


def load_env_file(env: str, directory: str | Path = ".") -> dict[str, str]:
    """
    Values from ``.env.<env>`` in ``directory``, or ``{}`` when there is no file.

    A missing file is the normal case in CI, where the same values arrive as real
    environment variables, so it is not worth a warning.
    """
    path = env_file_path(env, directory)
    if not path.is_file():
        return {}
    try:
        return parse_env_text(path.read_text(encoding="utf-8"))
    except OSError as exc:
        logger.warning("env file %s unreadable (%s), ignored", path, exc)
        return {}


def _value(rest: str) -> str:
    if len(rest) >= 2 and rest[0] == rest[-1] and rest[0] in ("'", '"'):
        inner = rest[1:-1]
        if rest[0] == "'":
            return inner
        return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(0)), inner)
    # Unquoted: an inline comment needs whitespace before the '#', so a value
    # such as a URL fragment or a password containing '#' survives.
    return re.split(r"\s+#", rest, maxsplit=1)[0].strip()
