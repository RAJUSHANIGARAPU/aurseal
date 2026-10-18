"""
The selector priority, as one ordered model instead of a sentence in a prompt.

A locator registry entry names *how* to find an element (``"type"``) and with
what. Not every way is equally durable. An attribute the team put there for
tests survives a redesign; an accessible name survives a restyle; a visible text
survives neither a copy change nor a translation; a CSS path breaks when anyone
touches the markup. The order, most to least durable:

====  ===============  ====================================================
rank  type             entry shape
====  ===============  ====================================================
0     ``data``         ``{"attribute": "data-key", "value": "save"}``
1     ``testid``       ``{"value": "save-button"}`` (``data-testid``)
2     ``aria_label``   ``{"value": "Save order"}`` (exact ``aria-label``)
3     ``label``        ``{"value": "Email"}`` (associated ``<label>``)
4     ``role``         ``{"role": "button", "name": "Save"}``
5     ``placeholder``  ``{"value": "Search"}``
6     ``text``         ``{"value": "Save"}``
7     ``css``          ``{"value": "form > button.primary"}``
====  ===============  ====================================================

``render`` turns an entry into a Playwright locator and is what
``BasePage.resolve`` uses, so the registry, the page objects and the healer
share one vocabulary. ``shape_error`` says why an entry cannot be rendered — the
healer checks a suggestion with it before writing anything. ``priority_prompt``
is the same order as text for an LLM prompt, and ``lint_registry`` flags an entry
that uses a lower-ranked strategy while its own ``"attributes"`` metadata records
a higher-ranked one that would work::

    "save_button": {"type": "css", "value": "form button.primary",
                    "attributes": {"data-testid": "save-order"}}
    # -> save_button: css (rank 7) while testid (rank 1) is available
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from enum import Enum


class LocatorStrategy(str, Enum):
    DATA = "data"
    TESTID = "testid"
    ARIA_LABEL = "aria_label"
    LABEL = "label"
    ROLE = "role"
    PLACEHOLDER = "placeholder"
    TEXT = "text"
    CSS = "css"

    @property
    def rank(self) -> int:
        """0 is the most durable."""
        return list(LocatorStrategy).index(self)

    @classmethod
    def of(cls, entry_or_type: Mapping | str) -> LocatorStrategy:
        kind = entry_or_type.get("type") if isinstance(entry_or_type, Mapping) else entry_or_type
        try:
            return cls(kind)
        except ValueError:
            raise ValueError(
                f"Unsupported locator type: {kind!r}; expected one of {[s.value for s in cls]}"
            ) from None


# Which metadata attribute would let an entry use which strategy.
_ATTRIBUTE_STRATEGY = {
    "data-testid": LocatorStrategy.TESTID,
    "aria-label": LocatorStrategy.ARIA_LABEL,
    "placeholder": LocatorStrategy.PLACEHOLDER,
}


def rank(entry_or_type: Mapping | str) -> int:
    """The priority rank of an entry (or a type name); lower is more durable."""
    return LocatorStrategy.of(entry_or_type).rank


def ranked(entries: Iterable[Mapping]) -> list[Mapping]:
    """``entries`` sorted most durable first; ties keep their order."""
    return sorted(entries, key=rank)


def priority_prompt() -> str:
    """The order as one line, for a prompt: ``data-* attribute > data-testid > ...``."""
    names = {
        LocatorStrategy.DATA: "data-* test attribute",
        LocatorStrategy.TESTID: "data-testid",
        LocatorStrategy.ARIA_LABEL: "aria-label",
        LocatorStrategy.LABEL: "label",
        LocatorStrategy.ROLE: "role",
        LocatorStrategy.PLACEHOLDER: "placeholder",
        LocatorStrategy.TEXT: "text",
        LocatorStrategy.CSS: "css",
    }
    return " > ".join(names[s] for s in LocatorStrategy)


def shape_error(entry: object) -> str:
    """Why ``entry`` cannot be rendered, or "" when it can."""
    if not isinstance(entry, Mapping):
        return f"expected a locator mapping, got {type(entry).__name__}"
    kind = entry.get("type")
    if not isinstance(kind, str) or not kind:
        return "locator has no 'type'"
    try:
        strategy = LocatorStrategy.of(kind)
    except ValueError:
        return f"unsupported locator type '{kind}' — base_page.resolve() would raise"
    if strategy is LocatorStrategy.ROLE:
        role = entry.get("role")
        if not isinstance(role, str) or not role.strip():
            return "role locator has no 'role'"
        return ""
    if strategy is LocatorStrategy.DATA:
        attribute = entry.get("attribute")
        if not isinstance(attribute, str) or not re.fullmatch(r"data-[a-z0-9-]+", attribute):
            return "data locator needs an 'attribute' like 'data-key'"
    value = entry.get("value")
    if not isinstance(value, str) or not value.strip():
        return f"{kind} locator has no usable 'value'"
    return ""


def _text_matcher(entry: Mapping, key: str = "value"):
    """A plain string, or a compiled pattern when the entry says ``"regex": true``."""
    value = entry[key]
    if entry.get("regex"):
        flags = re.IGNORECASE if entry.get("ignore_case", True) else 0
        return re.compile(value, flags)
    return value


def _attribute_selector(attribute: str, value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'[{attribute}="{escaped}"]'


def css_equivalent(entry: Mapping) -> str | None:
    """The CSS selector for an attribute strategy (data, testid, aria-label), else None."""
    strategy = LocatorStrategy.of(entry)
    if strategy is LocatorStrategy.DATA:
        return _attribute_selector(entry["attribute"], entry["value"])
    if strategy is LocatorStrategy.TESTID and not entry.get("regex"):
        return _attribute_selector("data-testid", entry["value"])
    if strategy is LocatorStrategy.ARIA_LABEL:
        return _attribute_selector("aria-label", entry["value"])
    if strategy is LocatorStrategy.CSS:
        return entry["value"]
    return None


def render(page, entry: Mapping):
    """The Playwright locator ``entry`` describes, on ``page`` (or any locator root)."""
    error = shape_error(entry)
    if error:
        raise ValueError(error)
    strategy = LocatorStrategy.of(entry)
    if strategy in (LocatorStrategy.DATA, LocatorStrategy.ARIA_LABEL, LocatorStrategy.CSS):
        return page.locator(css_equivalent(entry))
    if strategy is LocatorStrategy.TESTID:
        return page.get_by_test_id(_text_matcher(entry))
    if strategy is LocatorStrategy.LABEL:
        return page.get_by_label(_text_matcher(entry), exact=entry.get("exact", True))
    if strategy is LocatorStrategy.ROLE:
        kwargs: dict = {}
        if entry.get("name"):
            kwargs["name"] = _text_matcher(entry, "name")
        if entry.get("level"):
            kwargs["level"] = entry["level"]
        if "exact" in entry:
            kwargs["exact"] = entry["exact"]
        return page.get_by_role(entry["role"], **kwargs)
    if strategy is LocatorStrategy.PLACEHOLDER:
        return page.get_by_placeholder(entry["value"])
    return page.get_by_text(_text_matcher(entry), exact=entry.get("exact", True))


def better_available(entry: Mapping) -> LocatorStrategy | None:
    """The best strategy the entry's ``attributes`` metadata offers, when it beats the entry's."""
    attributes = entry.get("attributes") or {}
    if not isinstance(attributes, Mapping):
        return None
    options = []
    for attribute, value in attributes.items():
        if not isinstance(value, str) or not value.strip():
            continue
        if attribute in _ATTRIBUTE_STRATEGY:
            options.append(_ATTRIBUTE_STRATEGY[attribute])
        elif attribute.startswith("data-"):
            options.append(LocatorStrategy.DATA)
    if not options:
        return None
    best = min(options, key=lambda s: s.rank)
    return best if best.rank < rank(entry) else None


def lint_registry(registry: Mapping[str, Mapping]) -> list[str]:
    """
    One finding per entry that cannot be rendered, or that uses a lower-ranked
    strategy than its own ``attributes`` metadata makes available.
    """
    findings = []
    for key, entry in registry.items():
        error = shape_error(entry)
        if error:
            findings.append(f"{key}: {error}")
            continue
        better = better_available(entry)
        if better is not None:
            current = LocatorStrategy.of(entry)
            findings.append(
                f"{key}: {current.value} (rank {current.rank}) while "
                f"{better.value} (rank {better.rank}) is available"
            )
    return findings
