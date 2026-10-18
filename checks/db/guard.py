"""Statement guard: only plain reads get through.

Defence in depth, not a security boundary. The real guarantee on a shared
database is a database user that can only read; on SQLite the probe additionally
opens the file read-only, so the engine refuses writes whatever this says.

Accepted: one ``SELECT`` statement, or ``WITH ... SELECT``. Refused, after
comments and string literals are blanked out so neither can hide or fake a
keyword: more than one statement; any data-changing or schema-changing keyword
anywhere (a CTE can wrap an ``UPDATE``); ``SELECT ... INTO`` (creates a table on
some engines); row locks (``FOR UPDATE``/``FOR SHARE``).
"""

from __future__ import annotations

import re

_WRITE_WORDS = (
    "insert",
    "update",
    "delete",
    "merge",
    "upsert",
    "replace",
    "create",
    "drop",
    "alter",
    "truncate",
    "grant",
    "revoke",
    "attach",
    "detach",
    "pragma",
    "vacuum",
    "reindex",
    "analyze",
    "copy",
    "call",
    "exec",
    "execute",
    "lock",
    "set",
    "into",
    "refresh",
    "comment",
    "rename",
)
_WRITE_RE = re.compile(r"\b(" + "|".join(_WRITE_WORDS) + r")\b", re.IGNORECASE)
_LOCK_RE = re.compile(r"\bfor\s+(update|share|no\s+key\s+update|key\s+share)\b", re.IGNORECASE)
_FIRST_WORD = re.compile(r"^\s*\(*\s*([a-z]+)", re.IGNORECASE)


class WriteRefused(PermissionError):
    """A statement the read-only probe will not run."""


def _blank(sql: str) -> str:
    """Comments and quoted literals replaced by spaces (length kept for messages)."""
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        two = sql[i : i + 2]
        ch = sql[i]
        if two == "--":
            end = sql.find("\n", i)
            end = n if end == -1 else end
            out.append(" " * (end - i))
            i = end
        elif two == "/*":
            end = sql.find("*/", i + 2)
            end = n if end == -1 else end + 2
            out.append(" " * (end - i))
            i = end
        elif ch in ("'", '"', "`"):
            j = i + 1
            while j < n:
                if sql[j] == ch and sql[j + 1 : j + 2] == ch:  # doubled quote escape
                    j += 2
                    continue
                if sql[j] == ch:
                    break
                j += 1
            # Identifiers in double quotes / backticks stay visible as a placeholder word.
            filler = " " if ch == "'" else "x"
            out.append(ch + filler * (j - i - 1) + (ch if j < n else ""))
            i = j + 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def read_only_problem(sql: str) -> str | None:
    """Why ``sql`` is not a plain read, or None when it is."""
    if not isinstance(sql, str) or not sql.strip():
        return "empty statement"
    text = _blank(sql)
    body = text.strip().rstrip(";").strip()
    if ";" in body:
        return "more than one statement"
    first = _FIRST_WORD.match(body)
    if not first or first.group(1).lower() not in ("select", "with"):
        return f"not a SELECT: starts with {body.split()[0] if body.split() else '?'!r}"
    if first.group(1).lower() == "with" and not re.search(r"\bselect\b", body, re.IGNORECASE):
        return "WITH without a SELECT"
    word = _WRITE_RE.search(body)
    if word:
        return f"write keyword {word.group(1).upper()!r}"
    lock = _LOCK_RE.search(body)
    if lock:
        return f"row lock {lock.group(0).upper()!r}"
    return None


def require_read_only(sql: str) -> str:
    problem = read_only_problem(sql)
    if problem:
        raise WriteRefused(f"refused ({problem}): {' '.join(sql.split())[:200]}")
    return sql
