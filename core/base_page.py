import json
from pathlib import Path

from core.locator_strategy import render
from core.logger import logger

DEFAULT_LOCATOR_FILE = Path("ui/locators/wiki_locators.json")


class BasePage:
    def __init__(self, page, config, locator_file: str | Path | None = None):
        self.page = page
        self.config = config

        locator_path = Path(locator_file) if locator_file else DEFAULT_LOCATOR_FILE
        with open(locator_path) as f:
            self.locators = json.load(f)

    def resolve(self, key):
        """
        The Playwright locator for registry entry ``key``.

        Rendering lives in ``core.locator_strategy`` so the page objects, the
        healer and the registry lint share one vocabulary and one priority order.
        """
        return render(self.page, self.locators[key])

    def resolve_first_present(self, *keys):
        """
        The first of ``keys`` whose locator matches anything, tried in the order given.

        Use this rather than one locator that unions the alternatives. A union
        resolves by *document order*, not by the order the alternatives are written
        in, so a loose fallback that renders higher on the page beats the exact
        match listed first. Separate keys, asked in turn, keep the fallback from
        winning. When nothing matches, the last key's locator is returned so the
        caller's own "not present" handling and its error message still apply.
        """
        if not keys:
            raise ValueError("resolve_first_present needs at least one locator key")
        for index, key in enumerate(keys):
            candidate = self.resolve(key).first
            if candidate.count() > 0:
                if index:
                    logger.debug("locator '%s' matched nothing; resolved via '%s'", keys[0], key)
                return candidate
        return self.resolve(keys[-1]).first
