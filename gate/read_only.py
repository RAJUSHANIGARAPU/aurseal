"""A client that can only read, and a source check that keeps it that way.

Code that looks at an environment other people share should have no way to
modify it, neither on purpose nor after some future edit. Handing it a full
HTTP session leaves writing one method call away. Handing it a
:class:`ReadOnlyClient` removes ``post``/``put``/``patch``/``delete`` from what
it can call: the wrapper keeps only a closure over the session's ``get``, not
the session, so there is no attribute to reach through.

Python cannot make that absolute, so :func:`find_write_calls` checks the source
of the modules that must stay read-only (the counterpart of a bytecode check),
and a test asserts it returns nothing.

:func:`require_writable` is the environment half of the same policy: a
write-shaped probe (one that sends POST/PUT/DELETE even if the service must
refuse it) skips on an environment whose profile says ``read_only``.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import pytest

MUTATING_VERBS = frozenset({"post", "put", "patch", "delete"})

#: Modules that must stay read-only; a test runs :func:`find_write_calls` over them.
READ_ONLY_MODULES: tuple[str, ...] = (
    "integrations/read_transport.py",
    "integrations/requirements/base.py",
    "gate/read_only.py",
)


class ReadOnlyHttp(Protocol):
    def get(self, path: str, **kwargs: Any) -> Any: ...


class ReadOnlyClient:
    """GET (and HEAD) only, relative to ``base_url``."""

    __slots__ = ("_get", "_head", "base_url")

    def __init__(
        self,
        get: Callable[..., Any],
        base_url: str = "",
        head: Callable[..., Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        fixed = dict(headers or {})

        def _call(fn: Callable[..., Any], url: str, kwargs: dict[str, Any]) -> Any:
            if fixed:
                kwargs["headers"] = {**fixed, **(kwargs.get("headers") or {})}
            return fn(url, **kwargs)

        object.__setattr__(self, "base_url", base_url.rstrip("/"))
        object.__setattr__(self, "_get", lambda url, kw: _call(get, url, kw))
        object.__setattr__(
            self, "_head", (lambda url, kw: _call(head, url, kw)) if head is not None else None
        )

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("ReadOnlyClient is immutable")

    @classmethod
    def over(cls, session: Any, base_url: str = "", **kwargs: Any) -> ReadOnlyClient:
        """A read-only view of a requests-like session; the session itself is not kept."""
        return cls(session.get, base_url, head=getattr(session, "head", None), **kwargs)

    def url(self, path: str) -> str:
        if path.startswith(("http://", "https://")) or not self.base_url:
            return path
        return f"{self.base_url}/{path.lstrip('/')}"

    def get(self, path: str, **kwargs: Any) -> Any:
        return self._get(self.url(path), kwargs)

    def head(self, path: str, **kwargs: Any) -> Any:
        if self._head is None:
            raise NotImplementedError("this client was built without HEAD")
        return self._head(self.url(path), kwargs)


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def env_read_only(config: Any = None) -> bool:
    """The active environment's ``read_only`` flag, parsed strictly (a typo raises)."""
    if config is None:
        from core.config_reader import ConfigReader

        config = ConfigReader()
    value = config.get("read_only")
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError(f"read_only={value!r} is not a boolean")


def require_writable(what: str, *, read_only: bool | None = None, config: Any = None) -> None:
    """Skip ``what`` when the environment is read-only (``read_only`` overrides the config)."""
    protected = env_read_only(config) if read_only is None else read_only
    if protected:
        pytest.skip(f"{what} sends a write-shaped request; this environment is read-only")


@dataclass(frozen=True)
class WriteCall:
    path: str
    line: int
    call: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.call}"


def find_write_calls(
    paths: Iterable[str | Path], forbidden: Iterable[str] = MUTATING_VERBS
) -> list[WriteCall]:
    """Every ``<anything>.post(...)``-style call (and ``requests.request("POST", ...)``) found."""
    forbidden = frozenset(m.lower() for m in forbidden)
    hits: list[WriteCall] = []
    for raw in paths:
        path = Path(raw)
        files = sorted(path.rglob("*.py")) if path.is_dir() else [path]
        for file in files:
            tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                name = node.func.attr.lower()
                if name in forbidden:
                    hits.append(WriteCall(str(file), node.lineno, ast.unparse(node.func)))
                elif name == "request" and node.args:
                    method = node.args[0]
                    if (
                        isinstance(method, ast.Constant)
                        and isinstance(method.value, str)
                        and method.value.lower() in forbidden
                    ):
                        hits.append(WriteCall(str(file), node.lineno, ast.unparse(node)))
    return hits
