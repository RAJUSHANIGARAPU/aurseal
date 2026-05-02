"""Data models for parsed DOM elements."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ElementSpec:
    """One interactive element extracted from the DOM."""

    tag: str = ""  # input, button, select, textarea, a
    element_type: str = ""  # text, email, password, submit, checkbox, ...
    selector: str = ""  # best stable Playwright selector
    label: str = ""  # associated <label> text
    placeholder: str = ""
    name: str = ""
    element_id: str = ""
    href: str = ""  # for links
    required: bool = False
    input_pattern: str | None = None
    aria_label: str = ""
    text_content: str = ""
    is_visible: bool = True

    @property
    def display_name(self) -> str:
        return (
            self.label
            or self.aria_label
            or self.placeholder
            or self.name
            or self.text_content
            or self.element_id
            or self.element_type
            or self.tag
        )

    def is_form_field(self) -> bool:
        return self.tag in ("input", "textarea", "select") and self.element_type not in (
            "submit",
            "button",
            "reset",
            "image",
            "hidden",
        )

    def is_submit(self) -> bool:
        return self.element_type in ("submit", "button") or (
            self.tag == "button" and self.element_type not in ("reset",)
        )

    def is_link(self) -> bool:
        return self.tag == "a" and bool(self.href)


@dataclass
class FormSpec:
    """A <form> element with all its fields."""

    action: str = ""
    method: str = "GET"
    name: str = ""
    fields: list[ElementSpec] = field(default_factory=list)
    submit_selector: str = ""

    @property
    def required_fields(self) -> list[ElementSpec]:
        return [f for f in self.fields if f.required]

    @property
    def optional_fields(self) -> list[ElementSpec]:
        return [f for f in self.fields if not f.required]


@dataclass
class PageSpec:
    """All interactive elements discovered on one page."""

    url: str = ""
    title: str = ""
    depth: int = 0
    forms: list[FormSpec] = field(default_factory=list)
    standalone_elements: list[ElementSpec] = field(default_factory=list)
    links: list[ElementSpec] = field(default_factory=list)

    @property
    def all_form_fields(self) -> list[ElementSpec]:
        return [f for form in self.forms for f in form.fields]

    @property
    def all_elements(self) -> list[ElementSpec]:
        return self.all_form_fields + self.standalone_elements

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "depth": self.depth,
            "forms": [
                {
                    "action": f.action,
                    "method": f.method,
                    "fields": [_el_to_dict(el) for el in f.fields],
                    "submit_selector": f.submit_selector,
                }
                for f in self.forms
            ],
            "links": [_el_to_dict(el) for el in self.links],
            "standalone_elements": [_el_to_dict(el) for el in self.standalone_elements],
        }


def _el_to_dict(el: ElementSpec) -> dict:
    return {
        "tag": el.tag,
        "type": el.element_type,
        "selector": el.selector,
        "label": el.label,
        "placeholder": el.placeholder,
        "name": el.name,
        "id": el.element_id,
        "href": el.href,
        "required": el.required,
        "aria_label": el.aria_label,
        "text_content": el.text_content,
    }
