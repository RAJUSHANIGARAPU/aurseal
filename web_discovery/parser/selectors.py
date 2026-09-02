"""
Building selectors that survive the values found in a real DOM.

Selectors are the whole output of web discovery — they end up in generated test
files — and they were assembled with Python's ``repr()`` and, for ids, with no
escaping at all:

    return f"#{v}"                        # id
    return f"[data-testid={v!r}]"         # everything else

``repr()`` produces a *Python* literal. It coincides with CSS often enough to
look correct and is not the same grammar. ``#{v}`` is worse: an id is allowed to
contain characters that mean something else in a selector, so ``id="user.name"``
became ``#user.name`` — which reads as "element #user that also has class
name", quietly matching the wrong element or nothing, and ``id="2fa"`` became
``#2fa``, which is not a valid selector at all.

Ids like these are ordinary in real applications: Django renders ``id_user.name``
patterns, ASP.NET emits ``ctl00$Main$txt``, and any framework interpolating a
key into an id can produce a dot, a colon or a leading digit.

So values are quoted here as CSS strings, and the ``#id`` shorthand is used only
when the id is a plain identifier where it is unambiguous. ``[id="..."]`` is
always valid and is the fallback.
"""

from __future__ import annotations

import re

# A CSS identifier that needs no escaping: ASCII letters, digits, hyphen and
# underscore, not starting with a digit (nor a hyphen followed by a digit).
# Non-ASCII identifiers are legal in CSS but are sent through the quoted form
# instead — always correct, and not worth a second escaping implementation.
_PLAIN_IDENTIFIER = re.compile(r"^-?[A-Za-z_][A-Za-z0-9_-]*$")


def css_string(value: str) -> str:
    """
    Quote a value as a CSS string literal.

    Backslash and the quote character are escaped; control characters use the
    hexadecimal form the CSS grammar requires, which needs its trailing space
    to terminate the escape.
    """
    out: list[str] = []
    for char in value:
        if char in ('"', "\\"):
            out.append("\\" + char)
        elif char < " " or char == "\x7f":
            out.append(f"\\{ord(char):x} ")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def attribute_selector(name: str, value: str) -> str:
    """``[name="value"]`` with the value safely quoted."""
    return f"[{name}={css_string(value)}]"


def id_selector(element_id: str) -> str:
    """
    Select by id.

    Uses ``#id`` when that is unambiguous and ``[id="…"]`` when it is not, so a
    dot, colon, space or leading digit in an id cannot change what the selector
    means.
    """
    if _PLAIN_IDENTIFIER.match(element_id):
        return f"#{element_id}"
    return attribute_selector("id", element_id)


def text_selector(tag: str, text: str) -> str:
    """
    ``tag:has-text("…")``.

    Note for anyone reading generated output: Playwright's ``:has-text()`` is a
    case-insensitive *substring* match, so a button reading "Save" also matches
    "Save and close". That is deliberate here — it is the last resort in the
    priority order, reached only when an element carries no test id, aria-label,
    placeholder, id, name or role — but it means this selector is not guaranteed
    to be unique. Prefer giving the element a data-testid.
    """
    return f"{tag}:has-text({css_string(text)})"
