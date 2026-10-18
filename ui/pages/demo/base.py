"""
Shared base for the demo-service screens (``demo_sut``).

Every locator comes from ``ui/locators/demo_locators.json``. An entry whose value
holds ``{placeholders}`` is a template: ``resolve("customer.address_field", n=2,
field="postcode")`` fills it before rendering, so repeated rows are addressed by a
1-based index without building selectors in code.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core.base_page import BasePage
from core.locator_strategy import render
from ui.components.screen import Screen
from ui.components.wizard import settle

LOCATOR_FILE = Path(__file__).resolve().parents[2] / "locators" / "demo_locators.json"
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def template_fields(entry: dict[str, Any]) -> set[str]:
    """Placeholder names used anywhere in a locator entry's string values."""
    names: set[str] = set()
    for value in entry.values():
        if isinstance(value, str):
            names.update(_PLACEHOLDER.findall(value))
    return names


def fill_template(entry: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """The entry with every ``{name}`` replaced; a missing parameter is an error, not ''."""
    missing = template_fields(entry) - set(params)
    if missing:
        raise KeyError(f"locator template needs {sorted(missing)}")
    return {
        key: value.format(**params) if isinstance(value, str) else value
        for key, value in entry.items()
    }


class DemoPage(BasePage):
    """One demo screen. Subclasses set ``ready`` (the locator key that marks it loaded)."""

    ready: str = "page.title"

    def __init__(self, page, base_url: str, locator_file: str | Path = LOCATOR_FILE) -> None:
        super().__init__(page, config=None, locator_file=locator_file)
        self.base_url = base_url.rstrip("/")
        self.screen = Screen(page, self.base_url)

    # -- locating -----------------------------------------------------------------

    def resolve(self, key: str, /, **params: Any):
        entry = self.locators[key]
        if params or template_fields(entry):
            entry = fill_template(entry, params)
        return render(self.page, entry)

    def within(self, root, key: str, /, **params: Any):
        """``key`` rendered under ``root`` (a row, a dialog) instead of the whole page."""
        entry = self.locators[key]
        if params or template_fields(entry):
            entry = fill_template(entry, params)
        return render(root, entry)

    # -- navigation ---------------------------------------------------------------

    def goto(self, route: str) -> None:
        self.page.goto(self.base_url + route)
        self.resolve(self.ready).first.wait_for()

    def wait_ready(self) -> None:
        self.resolve(self.ready).first.wait_for()

    # -- common reads ---------------------------------------------------------------

    def text(self, key: str, **params: Any) -> str:
        return (self.resolve(key, **params).first.inner_text() or "").strip()

    def title(self) -> str:
        return self.text("page.title")

    def active_faults(self) -> list[str]:
        """The fault set the screen itself reports (empty list: none)."""
        value = self.resolve("page.faults_banner").first.get_attribute("data-faults") or ""
        return [name for name in value.split(",") if name]

    def error_banner(self) -> str:
        banner = self.resolve("page.error_banner").first
        return banner.inner_text().strip() if banner.is_visible() else ""

    def current_step(self) -> str:
        """The wizard step marked ``aria-current="step"`` in the step list."""
        steps = self.resolve("wizard.steps").first
        script = (
            "ol => { const li = [...ol.children].find("
            "c => c.getAttribute('aria-current') === 'step'); return li ? li.textContent : ''; }"
        )
        return (steps.evaluate(script) or "").strip()

    def settle(self, timeout_ms: int = 5000) -> bool:
        return settle(self.page, timeout_ms)
