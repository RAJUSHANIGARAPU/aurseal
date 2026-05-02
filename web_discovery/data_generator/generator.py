"""Field-aware test data generator using Faker."""

from __future__ import annotations

import random
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from web_discovery.parser.models import ElementSpec

try:
    from faker import Faker as _Faker

    _faker = _Faker()
except ImportError:
    _faker = None  # type: ignore[assignment]

_EMAIL_RE = re.compile(r"email", re.I)
_PHONE_RE = re.compile(r"phone|tel|mobile", re.I)
_DATE_RE = re.compile(r"date|dob|born|birth", re.I)
_NAME_RE = re.compile(r"^(first|last|full)?[\s_\-]?name", re.I)
_URL_RE = re.compile(r"url|website|link|href", re.I)
_ZIP_RE = re.compile(r"zip|postal|postcode", re.I)
_CITY_RE = re.compile(r"city|town|municipality", re.I)
_ADDR_RE = re.compile(r"address|street|addr", re.I)
_PASS_RE = re.compile(r"password|passwd|pwd", re.I)
_USER_RE = re.compile(r"username|user_?name|login", re.I)
_NUM_RE = re.compile(r"number|count|amount|qty|quantity|age|num", re.I)
_SEARCH_RE = re.compile(r"search|query|q", re.I)


def _label(el: ElementSpec) -> str:
    return (el.label or el.placeholder or el.name or el.aria_label or el.display_name).lower()


class DataGenerator:
    """Generates realistic test data for form fields."""

    def __init__(self, locale: str = "en_US", seed: int | None = None) -> None:
        if _faker is not None:
            from faker import Faker

            self._f = Faker(locale)
            if seed is not None:
                Faker.seed(seed)
        else:
            self._f = None
        self._seed = seed

    def generate_for_field(self, el: ElementSpec) -> str:
        el_type = (el.element_type or "text").lower()
        hint = _label(el)

        if el_type == "email" or _EMAIL_RE.search(hint):
            return self._email()
        if el_type == "password" or _PASS_RE.search(hint):
            return self._password()
        if el_type == "tel" or _PHONE_RE.search(hint):
            return self._phone()
        if el_type == "date" or _DATE_RE.search(hint):
            return self._date()
        if el_type == "number" or _NUM_RE.search(hint):
            return str(random.randint(1, 100))
        if el_type == "url" or _URL_RE.search(hint):
            return self._url()
        if el_type == "select":
            return self._select_option(el)
        if el_type == "checkbox":
            return "true"

        if _USER_RE.search(hint):
            return self._username()
        if _NAME_RE.search(hint):
            return self._name(hint)
        if _ZIP_RE.search(hint):
            return self._postcode()
        if _CITY_RE.search(hint):
            return self._city()
        if _ADDR_RE.search(hint):
            return self._address()
        if _SEARCH_RE.search(hint):
            return self._word()

        if el_type in ("textarea",):
            return self._paragraph()

        return self._word()

    # ------------------------------------------------------------------

    def _email(self) -> str:
        return self._f.email() if self._f else "test@example.com"

    def _password(self) -> str:
        if self._f:
            return self._f.password(length=12, special_chars=True)
        return "Test@12345!"

    def _phone(self) -> str:
        return self._f.phone_number() if self._f else "+1-555-0100"

    def _date(self) -> str:
        if self._f:
            return self._f.date(pattern="%Y-%m-%d")
        return "1990-01-01"

    def _url(self) -> str:
        return self._f.url() if self._f else "https://example.com"

    def _username(self) -> str:
        return self._f.user_name() if self._f else "testuser"

    def _name(self, hint: str) -> str:
        if not self._f:
            return "John Doe"
        if "first" in hint:
            return self._f.first_name()
        if "last" in hint:
            return self._f.last_name()
        return self._f.name()

    def _postcode(self) -> str:
        return self._f.postcode() if self._f else "12345"

    def _city(self) -> str:
        return self._f.city() if self._f else "Springfield"

    def _address(self) -> str:
        return self._f.street_address() if self._f else "123 Main St"

    def _paragraph(self) -> str:
        if self._f:
            return self._f.sentence(nb_words=10)
        return "Sample test input for automated testing."

    def _word(self) -> str:
        return self._f.word() if self._f else "testvalue"

    def _select_option(self, el: ElementSpec) -> str:
        return "option1"
