"""DOM parser — extracts structured element specs from a live Playwright page."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from web_discovery.parser.models import ElementSpec, FormSpec, PageSpec

if TYPE_CHECKING:
    from playwright.sync_api import Page

logger = logging.getLogger(__name__)

_SELECTOR_PRIORITY = [
    ("data-testid", lambda el: el.get_attribute("data-testid")),
    ("aria-label", lambda el: el.get_attribute("aria-label")),
    ("placeholder", lambda el: el.get_attribute("placeholder")),
    ("id", lambda el: el.get_attribute("id")),
    ("name", lambda el: el.get_attribute("name")),
]


class DomParser:
    """Extracts ElementSpec / FormSpec / PageSpec from a Playwright Page."""

    def parse(self, page: Page) -> PageSpec:
        url = page.url
        title = _safe(page.title)
        spec = PageSpec(url=url, title=title)

        try:
            spec.forms = self._parse_forms(page)
            spec.links = self._parse_links(page)
            spec.standalone_elements = self._parse_standalone(page, spec.forms)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[dom] parse error on %s: %s", url, exc)

        logger.debug(
            "[dom] %s: %d forms, %d links, %d standalone",
            url,
            len(spec.forms),
            len(spec.links),
            len(spec.standalone_elements),
        )
        return spec

    # ------------------------------------------------------------------

    def _parse_forms(self, page: Page) -> list[FormSpec]:
        forms: list[FormSpec] = []
        try:
            form_handles = page.locator("form").all()
        except Exception:  # noqa: BLE001
            return forms

        for form_el in form_handles:
            try:
                action = form_el.get_attribute("action") or ""
                method = (form_el.get_attribute("method") or "GET").upper()
                name = form_el.get_attribute("name") or ""

                fields: list[ElementSpec] = []
                for tag in ("input", "textarea", "select"):
                    for el in form_el.locator(tag).all() or []:
                        spec = self._extract_element(el, tag)
                        if spec and spec.element_type not in ("hidden",):
                            fields.append(spec)

                submit_sel = ""
                submit_candidates = form_el.locator(
                    "button[type=submit], input[type=submit], button:not([type])"
                ).all()
                if submit_candidates:
                    submit_sel = self._best_selector(submit_candidates[0], "button") or ""

                forms.append(
                    FormSpec(
                        action=action,
                        method=method,
                        name=name,
                        fields=fields,
                        submit_selector=submit_sel,
                    )
                )
            except Exception:  # noqa: BLE001
                continue

        return forms

    def _parse_links(self, page: Page) -> list[ElementSpec]:
        links: list[ElementSpec] = []
        try:
            for el in page.locator("a[href]").all():
                try:
                    href = el.get_attribute("href") or ""
                    if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
                        continue
                    text = _safe(el.inner_text).strip()[:120]
                    aria = el.get_attribute("aria-label") or ""
                    selector = self._best_selector(el, "a") or f"a:has-text({text!r})"
                    links.append(
                        ElementSpec(
                            tag="a",
                            element_type="link",
                            selector=selector,
                            text_content=text,
                            aria_label=aria,
                            href=href,
                        )
                    )
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass
        return links

    def _parse_standalone(self, page: Page, forms: list[FormSpec]) -> list[ElementSpec]:
        """Buttons/inputs NOT inside a <form> element."""
        standalone: list[ElementSpec] = []
        form_selectors = {f.submit_selector for f in forms if f.submit_selector}

        try:
            for tag in ("button", "input[type=button]", "[role=button]"):
                for el in page.locator(tag).all():
                    try:
                        if not el.is_visible():
                            continue
                        spec = self._extract_element(el, "button")
                        if spec and spec.selector not in form_selectors:
                            standalone.append(spec)
                    except Exception:  # noqa: BLE001
                        continue
        except Exception:  # noqa: BLE001
            pass

        return standalone

    # ------------------------------------------------------------------

    def _extract_element(self, el, tag: str) -> ElementSpec | None:
        try:
            el_type = (el.get_attribute("type") or tag).lower()
            label = self._find_label(el)
            placeholder = el.get_attribute("placeholder") or ""
            name = el.get_attribute("name") or ""
            el_id = el.get_attribute("id") or ""
            aria = el.get_attribute("aria-label") or ""
            required = el.get_attribute("required") is not None
            pattern = el.get_attribute("pattern")
            text = _safe(el.inner_text).strip()[:120]
            selector = self._best_selector(el, tag) or f"{tag}"

            return ElementSpec(
                tag=tag,
                element_type=el_type,
                selector=selector,
                label=label,
                placeholder=placeholder,
                name=name,
                element_id=el_id,
                required=required,
                input_pattern=pattern,
                aria_label=aria,
                text_content=text,
            )
        except Exception:  # noqa: BLE001
            return None

    def _best_selector(self, el, tag: str) -> str:
        """Build the most stable selector for an element, in priority order."""
        try:
            # 1. data-testid
            v = el.get_attribute("data-testid")
            if v:
                return f"[data-testid={v!r}]"

            # 2. aria-label
            v = el.get_attribute("aria-label")
            if v:
                return f"[aria-label={v!r}]"

            # 3. placeholder (for inputs)
            v = el.get_attribute("placeholder")
            if v:
                return f"[placeholder={v!r}]"

            # 4. id
            v = el.get_attribute("id")
            if v and not v.startswith(("ember", "mat-", "ng-", "cdk-", ":")):
                return f"#{v}"

            # 5. name
            v = el.get_attribute("name")
            if v:
                return f"[name={v!r}]"

            # 6. visible text for buttons/links
            if tag in ("button", "a"):
                text = _safe(el.inner_text).strip()
                if text and len(text) < 60:
                    return f"{tag}:has-text({text!r})"

            # 7. role
            role = el.get_attribute("role")
            if role:
                return f"[role={role!r}]"

        except Exception:  # noqa: BLE001
            pass
        return f"{tag}"

    def _find_label(self, el) -> str:
        """Find associated label text for a form field."""
        try:
            el_id = el.get_attribute("id")
            if el_id:
                page = el.page
                label_el = page.locator(f"label[for={el_id!r}]").first
                if label_el.count():
                    return _safe(label_el.inner_text).strip()
        except Exception:  # noqa: BLE001
            pass
        try:
            parent_label = el.locator("xpath=ancestor::label[1]").first
            if parent_label.count():
                return _safe(parent_label.inner_text).strip()[:80]
        except Exception:  # noqa: BLE001
            pass
        return ""


def _safe(fn) -> str:
    try:
        result = fn() if callable(fn) else fn
        return str(result) if result is not None else ""
    except Exception:  # noqa: BLE001
        return ""
