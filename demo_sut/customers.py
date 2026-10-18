"""Customers: the account holder of a subscription, its validation rules and issued numbers.

A customer is an ``INDIVIDUAL`` or a ``COMPANY`` with a display name, an optional e-mail
address and phone number, one billing address and an optional payout account (where a
credit is paid back to). A new customer starts ``pending_verification`` when the setting
``require_verified_customer`` is on; ``POST .../actions/verify`` marks it ``verified``.
"""

from __future__ import annotations

import re
from typing import Any

from demo_sut.core import Context, Invalid, NotFound

HOME_COUNTRY = "XA"  # a user-assigned ISO code: the demo's invented home country
NAME_LIMIT = 64
TRUNCATED_AT = 40  # where the customer_name_truncated fault cuts
POSTAL_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 -]{0,11}$")
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
PHONE_RE = re.compile(r"^\+?[\d ]{3,20}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{8,30}$")

CUSTOMER_TYPES = ("INDIVIDUAL", "COMPANY")
#: customer type -> party code used in outbound messages.
TYPE_CODES = {"INDIVIDUAL": "I1", "COMPANY": "C1"}
NUMBER_UNIT = "D1"

#: The demo's message catalogue for customers. Screens and API errors use these texts.
MESSAGES: dict[str, str] = {
    "type_invalid": "Customer type must be INDIVIDUAL or COMPANY.",
    "name_required": "Display name is required.",
    "name_too_long": f"Display name may be at most {NAME_LIMIT} characters.",
    "email_invalid": "E-mail address is not valid.",
    "phone_invalid": "Phone may contain digits, spaces and a leading +.",
    "line1_required": "Address line 1 is required.",
    "postal_code_required": "Postal code is required.",
    "postal_code_invalid": "Postal code may contain letters, digits, spaces and hyphens.",
    "city_required": "City is required.",
    "country_invalid": "Country must be a two-letter code.",
    "account_invalid": "Account number fails its check digits.",
    "not_verified": "Customer is not verified.",
    "email_for_delivery": "Invoices by e-mail need an e-mail address.",
}

EDITABLE = ("type", "display_name", "email", "phone", "billing_address", "payout_account")


class IdSequences:
    """Numbers the service issues: ``TYPE-UNIT-n``, strictly increasing per type."""

    PATTERN = re.compile(r"^(CUS|PAR)-D1-(\d+)$")

    @staticmethod
    def issue(prefix: str, row_id: int) -> str:
        return f"{prefix}-{NUMBER_UNIT}-{row_id}"


def iban_checksum_ok(value: str) -> bool:
    """The public mod-97 check (ISO 13616) on a compact account number."""
    compact = value.replace(" ", "").upper()
    if not IBAN_RE.match(compact):
        return False
    rearranged = compact[4:] + compact[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1


def validate_iban(value: str) -> str | None:
    """``None`` when the account passes mod-97, else the catalogue message."""
    return None if iban_checksum_ok(value) else MESSAGES["account_invalid"]


def make_iban(country: str = HOME_COUNTRY, bban: str = "SEED0000000001") -> str:
    """A checksum-valid account number for an invented account body (test-data helper)."""
    body = bban.upper() + country.upper() + "00"
    digits = "".join(str(int(ch, 36)) for ch in body)
    check = 98 - int(digits) % 97
    return f"{country.upper()}{check:02d}{bban.upper()}"


def validate_postal_code(value: str | None) -> str | None:
    """Free-form postal codes: 1-12 letters, digits, spaces or hyphens, starting with one of
    the first two. ``None`` when acceptable."""
    text = (value or "").strip()
    if not text:
        return MESSAGES["postal_code_required"]
    return None if POSTAL_CODE_RE.match(text) else MESSAGES["postal_code_invalid"]


def _address(raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = raw or {}
    return {
        "line1": str(raw.get("line1") or "").strip(),
        "line2": str(raw.get("line2") or "").strip(),
        "postal_code": str(raw.get("postal_code") or "").strip(),
        "city": str(raw.get("city") or "").strip(),
        "country": str(raw.get("country") or HOME_COUNTRY).strip().upper(),
    }


def validate(doc: dict[str, Any]) -> dict[str, str]:
    """Field path -> message for every rule the document breaks."""
    errors: dict[str, str] = {}
    if doc.get("type") not in CUSTOMER_TYPES:
        errors["type"] = MESSAGES["type_invalid"]
    name = str(doc.get("display_name") or "").strip()
    if not name:
        errors["display_name"] = MESSAGES["name_required"]
    elif len(name) > NAME_LIMIT:
        errors["display_name"] = MESSAGES["name_too_long"]
    email = doc.get("email")
    if email and not EMAIL_RE.match(str(email)):
        errors["email"] = MESSAGES["email_invalid"]
    phone = doc.get("phone")
    if phone and not (isinstance(phone, str) and PHONE_RE.match(phone)):
        errors["phone"] = MESSAGES["phone_invalid"]
    address = doc.get("billing_address") or {}
    if not address.get("line1"):
        errors["billing_address.line1"] = MESSAGES["line1_required"]
    message = validate_postal_code(address.get("postal_code"))
    if message:
        errors["billing_address.postal_code"] = message
    if not address.get("city"):
        errors["billing_address.city"] = MESSAGES["city_required"]
    if not COUNTRY_RE.match(str(address.get("country") or "")):
        errors["billing_address.country"] = MESSAGES["country_invalid"]
    if doc.get("payout_account"):
        message = validate_iban(doc["payout_account"])
        if message:
            errors["payout_account"] = message
    return errors


class CustomerService:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx

    def _check(self, doc: dict[str, Any]) -> None:
        errors = validate(doc)
        if errors:
            raise Invalid(next(iter(errors.values())), errors)

    def _normalise(self, doc: dict[str, Any]) -> None:
        doc["billing_address"] = _address(doc.get("billing_address"))
        if doc.get("payout_account"):
            doc["payout_account"] = str(doc["payout_account"]).replace(" ", "").upper()
        if isinstance(doc.get("display_name"), str):
            doc["display_name"] = doc["display_name"].strip()
            name = doc["display_name"]
            if self.ctx.fault("customer_name_truncated") and TRUNCATED_AT < len(name) <= NAME_LIMIT:
                doc["display_name"] = name[:TRUNCATED_AT]

    def create(self, body: dict[str, Any]) -> dict[str, Any]:
        doc: dict[str, Any] = {k: body.get(k) for k in EDITABLE}
        self._normalise(doc)
        self._check(doc)
        pending = self.ctx.settings["require_verified_customer"]
        doc.update(
            status="pending_verification" if pending else "verified",
            verified_at=None,
            created_at=self.ctx.stamp(),
            run_tag=self.ctx.run_tag,
        )
        saved = self.ctx.store.insert("customers", doc)
        saved["number"] = IdSequences.issue("CUS", saved["id"])
        return self.ctx.store.save("customers", saved)

    def get(self, customer_id: Any) -> dict[str, Any]:
        doc = self.ctx.store.get("customers", customer_id)
        if doc is None:
            raise NotFound("customer", customer_id)
        return doc

    def read(self, customer_id: Any) -> dict[str, Any]:
        """One customer as the API returns it. Every read refreshes the read cache; only with
        the customer_read_cached_after_update fault is a cached copy served."""
        key = f"customer:{customer_id}"
        if self.ctx.fault("customer_read_cached_after_update") and key in self.ctx.cache:
            return dict(self.ctx.cache[key])
        doc = self.get(customer_id)
        self.ctx.cache[key] = dict(doc)
        return doc

    def list(self) -> list[dict[str, Any]]:
        return self.ctx.store.find("customers")

    def patch(self, customer_id: Any, body: dict[str, Any]) -> dict[str, Any]:
        doc = self.get(customer_id)
        for key in EDITABLE:
            if key in body:
                doc[key] = body[key]
        self._normalise(doc)
        self._check(doc)
        doc["updated_at"] = self.ctx.stamp()
        return self.ctx.store.save("customers", doc)

    def verify(self, customer_id: Any) -> dict[str, Any]:
        doc = self.get(customer_id)
        doc.update(status="verified", verified_at=self.ctx.stamp())
        return self.ctx.store.save("customers", doc)

    def require_usable(self, customer_id: Any) -> dict[str, Any]:
        """A customer a subscription may be written for (verified when the setting asks)."""
        doc = self.get(customer_id)
        if self.ctx.settings["require_verified_customer"] and doc["status"] != "verified":
            raise Invalid(MESSAGES["not_verified"], {"customer_id": MESSAGES["not_verified"]})
        return doc


def holder_of(customer: dict[str, Any]) -> dict[str, Any]:
    """How a subscription shows its holder: always read from the current customer record."""
    return {
        "id": customer["id"],
        "number": customer["number"],
        "name": customer["display_name"],
        "type": customer["type"],
        "email": customer.get("email"),
        "phone": customer.get("phone"),
    }
