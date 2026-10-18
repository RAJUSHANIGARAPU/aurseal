"""Subscriptions: lifecycle, versions, drafts (change / cancel / renew / note) and search.

A subscription is a team software plan billed per period. Lifecycle (see
``config/subscription.machine.yaml``)::

    draft --submit--> pending_payment --confirm--> active
    draft --activate (activate_immediately)--> active
    active|renewed --open change/cancel/renew draft--> changing
    changing --confirm--> active | renewed (renew) | cancelled (closure)
    changing --discard--> previous status

Every confirmed draft except a note writes a new *version* with an ``effective_from``
date. The *current* version is the latest one in effect on the demo clock, so a
future-dated change leaves the current version as it is until its date arrives.

The holder shown on a subscription is always read from the current customer record.
"""

from __future__ import annotations

import base64
import copy
import json
import re
from datetime import date
from decimal import Decimal
from typing import Any

from demo_sut.billing import (
    ADDONS,
    Ledger,
    index_price,
    monthly_net,
    parse_start,
    price_block,
    price_quote,
    pro_rata,
    share_of,
)
from demo_sut.core import (
    FREQUENCY_MONTHS,
    BadRequest,
    Conflict,
    Context,
    Invalid,
    NotFound,
    Throttled,
    add_months,
    add_years_clamped,
    iso,
    money,
    num,
    overlap,
    parse_date,
)
from demo_sut.customers import MESSAGES as CUSTOMER_MESSAGES
from demo_sut.customers import CustomerService, holder_of, validate_iban

DATA_REGIONS = ("EU-CENTRAL", "EU-NORTH", "AP-SOUTH")
WORKSPACE_TYPES = ("TEAM_SPACE", "SANDBOX", "TRAINING")
INVOICE_DELIVERY = ("EMAIL", "PORTAL")
PAYMENT_TYPES = ("CARD", "BANK_TRANSFER", "DIRECT_DEBIT")
CLOSURE_REASONS = ("NO_LONGER_NEEDED", "BUDGET", "CONSOLIDATING", "OTHER")
LABELS = {
    "L-PILOT": "Pilot",
    "L-INTERNAL": "Internal use",
    "L-GRANT": "Grant funded",
}
DRAFT_KINDS = ("change", "cancel", "renew", "note")
#: fields a change may not touch: they are fixed for the term.
LOCKED_IN_CHANGE = ("currency", "plan_id", "frequency")
PO_LIMIT = 35
PO_RE = re.compile(r"^[A-Za-z0-9 ./-]{1,35}$")
CURRENCY = "EUR"

MESSAGES = {
    "draft_exists": "A draft already exists for this subscription.",
    "not_active": "Subscription is not active.",
    "not_draft": "Only a draft subscription can be edited.",
    "locked": "{name} cannot be edited in a change.",
    "renewal_not_due": "Renewal is not due yet.",
    "renewal_unbooked": "Transactions before the term end are not all booked.",
    "reason_invalid": "Closure reason must be a code from the list.",
    "no_payee": "There is no account to pay the credit to.",
    "acceptance_open": "The new owner has not accepted the subscription yet.",
    "start_locked": "Start date cannot change after the first booking.",
    "frequency_invalid": "Frequency must be MONTHLY, QUARTERLY or YEARLY.",
    "region_invalid": "Data region must be one of the offered regions.",
    "workspace_invalid": "Workspace type must be one of the offered types.",
    "delivery_invalid": "Invoice delivery must be EMAIL or PORTAL.",
    "payment_invalid": "Payment method type must be CARD, BANK_TRANSFER or DIRECT_DEBIT.",
    "account_required": "Direct debit needs an account number.",
    "po_too_long": f"PO reference may be at most {PO_LIMIT} characters.",
    "po_invalid": "PO reference may contain letters, digits, spaces, '.', '/' and '-'.",
    "seats_invalid": "Every seat block needs a name and at least one seat.",
    "spend_cap_invalid": "Spend cap must not be negative.",
    "label_unknown": "Label must be a code from the catalogue.",
    "note_unknown": "Invoice note {number} does not exist.",
    "share_over_cap": "Reseller share may not exceed the cap.",
    "date_invalid": "Start must be a valid calendar date.",
    "throttled": "Too many subscriptions created at once; try again.",
    "title_required": "Title is required.",
}

#: Fields a version snapshot carries (everything except lifecycle bookkeeping).
SNAPSHOT_FIELDS = (
    "customer_id",
    "partner_id",
    "plan_id",
    "plan_kind",
    "frequency",
    "currency",
    "start_date",
    "term_start",
    "term_end",
    "spend_cap",
    "addons",
    "data_region",
    "workspace_type",
    "invoice_notes",
    "labels",
    "seat_blocks",
    "seat_total",
    "billing_contact",
    "payment_method",
    "account_check",
    "po_reference",
    "invoice_delivery",
    "price",
    "price_adjustment",
    "commission_percentage",
    "fees",
    "partner",
    "closure",
)


def next_term_end(term_end: date) -> date:
    """A renewal extends the term by exactly one calendar year (29 Feb clamps to 28 Feb)."""
    return add_years_clamped(term_end, 1)


def _locked_error(name: str) -> Invalid:
    message = MESSAGES["locked"].format(name=name)
    return Invalid(message, {name: message})


def _seat_blocks(rows: Any) -> list[dict[str, Any]]:
    blocks = []
    for row in rows or []:
        name = str((row or {}).get("name") or "").strip()
        seats = (row or {}).get("seats")
        if not name or isinstance(seats, bool) or not isinstance(seats, int) or seats < 1:
            raise Invalid(MESSAGES["seats_invalid"], {"seat_blocks": MESSAGES["seats_invalid"]})
        blocks.append({"name": name, "seats": seats})
    return blocks


def _payment_method(raw: Any) -> dict[str, Any]:
    raw = dict(raw or {})
    kind = raw.get("type")
    if kind not in PAYMENT_TYPES:
        raise Invalid(
            MESSAGES["payment_invalid"], {"payment_method.type": MESSAGES["payment_invalid"]}
        )
    out: dict[str, Any] = {"type": kind}
    account = str(raw.get("account") or "").replace(" ", "").upper()
    if kind == "DIRECT_DEBIT" and not account:
        raise Invalid(
            MESSAGES["account_required"], {"payment_method.account": MESSAGES["account_required"]}
        )
    if account:
        message = validate_iban(account)
        if message:
            raise Invalid(message, {"payment_method.account": message})
        out["account"] = account
    return out


def _choice(value: Any, allowed: tuple[str, ...], key: str, message: str, *, optional: bool):
    if value is None and optional:
        return None
    if value not in allowed:
        raise Invalid(MESSAGES[message], {key: MESSAGES[message]})
    return value


class SubscriptionService:
    def __init__(self, ctx: Context, ledger: Ledger, customers: CustomerService, docs: Any):
        self.ctx = ctx
        self.ledger = ledger
        self.customers = customers
        self.docs = docs

    # -- lookups -----------------------------------------------------------

    def _sub(self, sub_id: Any) -> dict[str, Any]:
        sub = self.ctx.store.get("subscriptions", sub_id)
        if sub is None:
            raise NotFound("subscription", sub_id)
        return sub

    def _ref(self, table: str, name: str, ident: Any) -> dict[str, Any]:
        doc = self.ctx.store.get(table, ident)
        if doc is None:
            raise Invalid(f"Unknown {name}.", {f"{name}_id": f"Unknown {name}."})
        return doc

    def versions(self, sub_id: Any) -> list[dict[str, Any]]:
        self._sub(sub_id)
        rows = self.ctx.store.find("versions", subscription_id=int(sub_id))
        return sorted(rows, key=lambda v: v["version"])

    def current_version(self, sub_id: Any) -> int:
        today = iso(self.ctx.clock.today())
        rows = self.versions(sub_id)
        in_effect = [v for v in rows if v["effective_from"] <= today]
        return (in_effect[-1] if in_effect else rows[0])["version"]

    def holder(self, customer_id: Any) -> dict[str, Any] | None:
        customer = self.ctx.store.get("customers", customer_id)
        return holder_of(customer) if customer else None

    def view(self, sub_id: Any, version: Any = None) -> dict[str, Any]:
        sub = self._sub(sub_id)
        rows = {v["version"]: v for v in self.versions(sub_id)}
        current = self.current_version(sub_id)
        wanted = current if version in (None, "") else int(version)
        if wanted not in rows:
            raise NotFound("version", f"{sub_id}/{version}")
        snap = rows[wanted]
        if wanted == current:
            version_status = "current"
        else:
            version_status = "superseded" if wanted < current else "scheduled"
        fees = dict(snap["fees"])
        fees["invoiced"] = any(
            t["state"] == "BOOKED" and t["fee_net"] for t in self.ledger.transactions(sub["id"])
        )
        return {
            **{k: snap.get(k) for k in SNAPSHOT_FIELDS},
            "fees": fees,
            "holder": self.holder(snap.get("customer_id")),
            "renewal_date": snap.get("term_end"),
            "id": sub["id"],
            "number": sub["number"],
            "status": sub["status"],
            "version_status": version_status,
            "version": wanted,
            "version_id": snap["id"],
            "effective_from": snap["effective_from"],
            "current": wanted == current,
            "current_version": current,
            "latest_version": max(rows),
            "open_draft_id": sub.get("open_draft_id"),
            "created_at": sub["created_at"],
        }

    # -- create / edit ------------------------------------------------------

    def create(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.ctx.fault("create_throttled_every_other") and self.ctx.tick("create") % 2 == 0:
            raise Throttled(MESSAGES["throttled"], retry_after=0)
        sub = {
            "status": "draft",
            "version": 1,
            "currency": CURRENCY,
            "spend_cap": 0.0,
            "addons": {key: False for key in ADDONS},
            "data_region": "EU-CENTRAL",
            "workspace_type": None,
            "invoice_notes": [],
            "labels": [],
            "seat_blocks": [],
            "seat_total": 0,
            "billing_contact": None,
            "payment_method": {"type": "CARD"},
            "account_check": None,
            "po_reference": None,
            "invoice_delivery": "EMAIL",
            "closure": None,
            "frequency": "MONTHLY",
            "open_draft_id": None,
            "created_at": self.ctx.stamp(),
            "run_tag": self.ctx.run_tag,
        }
        self._apply_fields(sub, body, creating=True)
        saved = self.ctx.store.insert("subscriptions", sub)
        saved["number"] = f"SUB-{saved['id']:06d}"
        self.ctx.store.save("subscriptions", saved)
        self._write_version(saved, saved["start_date"], "create")
        if saved.get("activate_immediately"):
            return self.confirm(saved["id"])
        return self.view(saved["id"])

    def _apply_fields(self, sub: dict[str, Any], body: dict[str, Any], creating: bool) -> None:
        if "currency" in body and body["currency"] != CURRENCY:
            raise Invalid(
                f"Currency must be {CURRENCY}.", {"currency": f"Currency must be {CURRENCY}."}
            )
        if creating or "customer_id" in body:
            customer = self.customers.require_usable(body.get("customer_id"))
            sub["customer_id"] = customer["id"]
        if creating or "partner_id" in body:
            partner = self._ref("partners", "partner", body.get("partner_id"))
            sub["partner_id"] = partner["id"]
            sub["partner"] = {
                "id": partner["id"],
                "number": partner["number"],
                "name": partner["name"],
            }
            if "commission_percentage" not in body:
                sub["commission_percentage"] = self._terms_share(partner)
        if creating or "plan_id" in body:
            plan = self._ref("plans", "plan", body.get("plan_id"))
            sub["plan_id"] = plan["id"]
            sub["plan_kind"] = plan["kind"]
        if "frequency" in body:
            if body["frequency"] not in FREQUENCY_MONTHS:
                raise Invalid(
                    MESSAGES["frequency_invalid"], {"frequency": MESSAGES["frequency_invalid"]}
                )
            sub["frequency"] = body["frequency"]
        if creating or "start_date" in body or "start_time" in body:
            raw = body.get("start_time") or body.get("start_date") or iso(self.ctx.clock.today())
            start = parse_start(raw)
            if (start.month, start.day) == (2, 29) and self.ctx.fault(
                "date_parser_rejects_leap_day"
            ):
                raise Invalid(MESSAGES["date_invalid"], {"start_date": MESSAGES["date_invalid"]})
            sub["start_date"] = sub["term_start"] = iso(start)
            sub["term_end"] = iso(add_months(start, int(body.get("term_months", 12))))
        self._apply_content(sub, body)
        if "commission_percentage" in body:
            share = Decimal(str(body["commission_percentage"] or 0))
            cap = Decimal(str(self.ctx.settings["commission_cap_percent"]))
            if share < 0 or share > cap:
                raise Invalid(
                    MESSAGES["share_over_cap"],
                    {"commission_percentage": MESSAGES["share_over_cap"]},
                )
            sub["commission_percentage"] = float(share)
        if "activate_immediately" in body:
            sub["activate_immediately"] = bool(body["activate_immediately"])
        self._check_delivery(sub)
        self._reprice(sub, body.get("monthly_net"), body.get("tax_rate"))

    def _apply_content(self, sub: dict[str, Any], body: dict[str, Any]) -> None:
        """The editable content fields, shared by create, draft edit and change."""
        if "spend_cap" in body:
            cap = money(body["spend_cap"] or 0)
            if cap < 0:
                raise Invalid(
                    MESSAGES["spend_cap_invalid"], {"spend_cap": MESSAGES["spend_cap_invalid"]}
                )
            sub["spend_cap"] = num(cap)
        if "addons" in body:
            sub["addons"] = {
                **sub.get("addons", {}),
                **{k: bool(v) for k, v in (body["addons"] or {}).items() if k in ADDONS},
            }
        if "data_region" in body:
            sub["data_region"] = _choice(
                body["data_region"], DATA_REGIONS, "data_region", "region_invalid", optional=False
            )
        if "workspace_type" in body:
            sub["workspace_type"] = _choice(
                body["workspace_type"],
                WORKSPACE_TYPES,
                "workspace_type",
                "workspace_invalid",
                optional=True,
            )
        if "invoice_delivery" in body:
            sub["invoice_delivery"] = _choice(
                body["invoice_delivery"],
                INVOICE_DELIVERY,
                "invoice_delivery",
                "delivery_invalid",
                optional=False,
            )
        if "invoice_notes" in body:
            sub["invoice_notes"] = [
                {"number": i + 1, "version": 1, "text": str((n or {}).get("text", ""))}
                for i, n in enumerate(body["invoice_notes"] or [])
            ]
        for row in body.get("add_invoice_notes") or []:
            number = max((n["number"] for n in sub["invoice_notes"]), default=0) + 1
            sub["invoice_notes"].append(
                {"number": number, "version": 1, "text": str(row.get("text", ""))}
            )
        for row in body.get("amend_invoice_notes") or []:
            match = [n for n in sub["invoice_notes"] if n["number"] == row.get("number")]
            if not match:
                message = MESSAGES["note_unknown"].format(number=row.get("number"))
                raise Invalid(message, {"amend_invoice_notes": message})
            match[0].update(text=str(row.get("text", "")), version=match[0]["version"] + 1)
        if "labels" in body or body.get("add_labels"):
            codes = (
                list(body.get("labels") or [])
                if "labels" in body
                else [row["code"] for row in sub["labels"]]
            )
            codes += list(body.get("add_labels") or [])
            if any(c not in LABELS for c in codes):
                raise Invalid(MESSAGES["label_unknown"], {"labels": MESSAGES["label_unknown"]})
            sub["labels"] = [{"code": c, "title": LABELS[c]} for c in codes]
        if "seat_blocks" in body:
            sub["seat_blocks"] = _seat_blocks(body["seat_blocks"])
        sub["seat_total"] = sum(b["seats"] for b in sub.get("seat_blocks") or [])
        if "billing_contact" in body:
            contact = body["billing_contact"]
            sub["billing_contact"] = (
                {"name": str(contact.get("name") or ""), "email": contact.get("email")}
                if contact
                else None
            )
        if "payment_method" in body:
            sub["payment_method"] = _payment_method(body["payment_method"])
        if "po_reference" in body:
            po = body["po_reference"]
            if po is not None and len(str(po)) > PO_LIMIT:
                raise Invalid(MESSAGES["po_too_long"], {"po_reference": MESSAGES["po_too_long"]})
            if po not in (None, "") and not PO_RE.match(str(po)):
                raise Invalid(MESSAGES["po_invalid"], {"po_reference": MESSAGES["po_invalid"]})
            sub["po_reference"] = None if po in (None, "") else str(po)

    def _check_delivery(self, sub: dict[str, Any]) -> None:
        if sub.get("invoice_delivery") != "EMAIL":
            return
        customer = self.ctx.store.get("customers", sub.get("customer_id")) or {}
        if not customer.get("email"):
            message = CUSTOMER_MESSAGES["email_for_delivery"]
            raise Invalid(message, {"invoice_delivery": message})

    def _terms_share(self, partner: dict[str, Any]) -> float:
        terms = self.ctx.store.get("reseller_terms", partner.get("reseller_terms_id"))
        return float((terms or {}).get("share_percent") or 0)

    def _reprice(self, sub: dict[str, Any], explicit_net: Any = None, tax_rate: Any = None):
        plan = self.ctx.store.get("plans", sub["plan_id"]) or {}
        base = monthly_net(plan)
        if explicit_net is not None:
            sub["price_adjustment"] = num(money(explicit_net) - base)
        adjustment = Decimal(str(sub.get("price_adjustment") or 0))
        rate = (
            tax_rate
            if tax_rate is not None
            else (sub.get("price") or {}).get("tax_rate", plan.get("tax_rate", 20))
        )
        sub["price"] = price_block(base + adjustment, rate, sub["frequency"])
        fee = Decimal(str(plan.get("setup_fee", 0)))
        sub["fees"] = {"net": num(fee), "tax": num(money(fee * Decimal(str(rate)) / 100))}

    def patch(self, sub_id: Any, body: dict[str, Any]) -> dict[str, Any]:
        sub = self._sub(sub_id)
        if sub["status"] != "draft":
            raise Conflict(MESSAGES["not_draft"])
        self._apply_fields(sub, body, creating=False)
        self.ctx.store.save("subscriptions", sub)
        self._write_version(sub, sub["start_date"], "edit", replace=True)
        return self.view(sub["id"])

    def _write_version(self, sub: dict, effective: str, cause: str, replace: bool = False):
        snap = {k: copy.deepcopy(sub.get(k)) for k in SNAPSHOT_FIELDS}
        doc = {
            "subscription_id": sub["id"],
            "version": sub["version"],
            "effective_from": effective,
            "cause": cause,
            "created_at": self.ctx.stamp(),
            **snap,
        }
        if replace:
            existing = self.ctx.store.find(
                "versions", subscription_id=sub["id"], version=sub["version"]
            )
            if existing:
                doc["id"] = existing[0]["id"]
                self.ctx.store.save("versions", doc)
                return
        self.ctx.store.insert("versions", doc)

    # -- lifecycle ---------------------------------------------------------

    def submit(self, sub_id: Any, body: dict[str, Any] | None = None) -> dict[str, Any]:
        sub = self._sub(sub_id)
        if sub["status"] != "draft":
            raise Conflict(MESSAGES["not_draft"])
        body = body or {}
        if "net_amount" in body:
            quote = price_quote(self.ctx, body)
            sub["commission_percentage"] = quote["commission_percentage"]
            self._reprice(sub, quote["net_amount"], quote["tax_rate"])
        sub["status"] = "pending_payment"
        self.ctx.store.save("subscriptions", sub)
        self._write_version(sub, sub["start_date"], "edit", replace=True)
        self.docs.issue(sub, "order_summary")
        return self.view(sub["id"])

    def confirm(self, sub_id: Any) -> dict[str, Any]:
        sub = self._sub(sub_id)
        allowed = sub["status"] == "pending_payment" or (
            sub["status"] == "draft" and sub.get("activate_immediately")
        )
        if not allowed:
            raise Conflict("Subscription cannot be confirmed in status " + sub["status"] + ".")
        sub["status"] = "active"
        sub["activated_at"] = self.ctx.stamp()
        self.ctx.store.save("subscriptions", sub)
        plan = self.ctx.store.get("plans", sub["plan_id"]) or {}
        self.ledger.schedule(
            sub,
            "NEW",
            date.fromisoformat(sub["term_start"]),
            date.fromisoformat(sub["term_end"]),
            setup_fee=Decimal(str(plan.get("setup_fee", 0))),
        )
        self.docs.issue(sub, "account_opened")
        out = self.view(sub["id"])
        out["scheduled_transactions"] = self.ledger.transactions(sub["id"])
        return out

    # -- drafts --------------------------------------------------------------

    def _draft(self, draft_id: Any) -> dict[str, Any]:
        draft = self.ctx.store.get("drafts", draft_id)
        if draft is None:
            raise NotFound("draft", draft_id)
        return draft

    def open_draft(self, sub_id: Any, body: dict[str, Any]) -> dict[str, Any]:
        sub = self._sub(sub_id)
        kind = body.get("kind")
        if kind not in DRAFT_KINDS:
            raise BadRequest(f"kind must be one of {list(DRAFT_KINDS)}")
        draft = {
            "subscription_id": sub["id"],
            "kind": kind,
            "status": "OPEN",
            "effective_date": body.get("effective_date") or iso(self.ctx.clock.today()),
            "changes": {},
            "reason": body.get("reason"),
            "index_percent": body.get("index_percent"),
            "title": body.get("title"),
            "description": body.get("description"),
            "ownership_accepted": False,
            "created_at": self.ctx.stamp(),
        }
        parse_date(draft["effective_date"], "effective_date")
        if kind != "note":
            if sub["status"] not in ("active", "renewed"):
                if sub.get("open_draft_id"):
                    raise Conflict(MESSAGES["draft_exists"], draft_id=sub["open_draft_id"])
                raise Conflict(MESSAGES["not_active"])
            if kind == "renew":
                self._check_renewable(sub)
            self._check_changes(body.get("changes") or {})
            draft["changes"] = dict(body.get("changes") or {})
        saved = self.ctx.store.insert("drafts", draft)
        if kind != "note":
            sub.update(prior_status=sub["status"], status="changing", open_draft_id=saved["id"])
            self.ctx.store.save("subscriptions", sub)
        return self.draft_view(saved["id"])

    def _check_changes(self, changes: dict[str, Any]) -> None:
        for name in LOCKED_IN_CHANGE:
            if name in changes:
                raise _locked_error(name)

    def _check_renewable(self, sub: dict[str, Any]) -> None:
        term_end = date.fromisoformat(sub["term_end"])
        if (term_end - self.ctx.clock.today()).days > self.ctx.settings["renewal_window_days"]:
            raise Invalid(MESSAGES["renewal_not_due"])
        for tx in self.ledger.transactions(sub["id"], live_only=True):
            if tx["period_start"] < sub["term_end"] and tx["state"] != "BOOKED":
                raise Invalid(MESSAGES["renewal_unbooked"])

    def draft_view(self, draft_id: Any) -> dict[str, Any]:
        draft = self._draft(draft_id)
        sub = self._sub(draft["subscription_id"])
        out = dict(draft)
        out["steps"] = self._steps(draft, sub)
        if draft["kind"] == "renew":
            out["preview"] = self._renew_preview(sub, draft)
        elif draft["kind"] == "change":
            head = self._applied(sub, draft["changes"])
            out["preview"] = {"price": head["price"], "addons": head["addons"]}
        return out

    def _steps(self, draft: dict[str, Any], sub: dict[str, Any]) -> list[str]:
        if draft["kind"] == "note":
            return ["note"]
        if draft["kind"] == "cancel":
            return ["details", "credit"]
        if draft["kind"] == "renew":
            return ["indexation", "price"]
        steps = ["details", "price"]
        new_owner = draft["changes"].get("customer_id")
        if new_owner is not None and int(new_owner) != sub["customer_id"]:
            steps.append("ownership_acceptance")
        return steps

    def patch_draft(self, draft_id: Any, body: dict[str, Any]) -> dict[str, Any]:
        draft = self._draft(draft_id)
        if draft["status"] != "OPEN":
            raise Conflict("Draft is not open.")
        changes = body.get("changes") or {}
        self._check_changes(changes)
        draft["changes"].update(changes)
        for key in ("effective_date", "reason", "index_percent", "title", "description"):
            if key in body:
                draft[key] = body[key]
        self.ctx.store.save("drafts", draft)
        return self.draft_view(draft["id"])

    def accept_ownership(self, draft_id: Any) -> dict[str, Any]:
        """The new owner's acceptance: required before an ownership transfer can be confirmed."""
        draft = self._draft(draft_id)
        if draft["status"] != "OPEN":
            raise Conflict("Draft is not open.")
        new_owner = draft["changes"].get("customer_id")
        if new_owner is not None:
            self.customers.require_usable(new_owner)
        draft["ownership_accepted"] = True
        self.ctx.store.save("drafts", draft)
        return self.draft_view(draft["id"])

    def discard_draft(self, draft_id: Any) -> dict[str, Any]:
        draft = self._draft(draft_id)
        if draft["status"] == "OPEN":
            draft["status"] = "DISCARDED"
            self.ctx.store.save("drafts", draft)
            self._close(draft)
        return self.draft_view(draft["id"])

    def _close(self, draft: dict[str, Any], status: str | None = None) -> dict[str, Any]:
        sub = self._sub(draft["subscription_id"])
        if draft["kind"] != "note" and sub.get("open_draft_id") == draft["id"]:
            sub["status"] = status or sub.get("prior_status") or "active"
            sub["open_draft_id"] = None
            self.ctx.store.save("subscriptions", sub)
        return sub

    def confirm_draft(self, draft_id: Any) -> dict[str, Any]:
        draft = self._draft(draft_id)
        if draft["status"] != "OPEN":
            raise Conflict("Draft is not open.")
        handler = {
            "change": self._confirm_change,
            "cancel": self._confirm_cancel,
            "renew": self._confirm_renew,
            "note": self._confirm_note,
        }[draft["kind"]]
        result = handler(draft)
        draft["status"] = "CONFIRMED"
        draft["confirmed_at"] = self.ctx.stamp()
        self.ctx.store.save("drafts", draft)
        return result

    def _confirm_note(self, draft: dict[str, Any]) -> dict[str, Any]:
        if not str(draft.get("title") or "").strip():
            raise Invalid(MESSAGES["title_required"], {"title": MESSAGES["title_required"]})
        return self.ctx.store.insert(
            "notes",
            {
                "subscription_id": draft["subscription_id"],
                "title": draft["title"],
                "description": draft.get("description") or "",
                "created_at": self.ctx.stamp(),
            },
        )

    def notes(self, sub_id: Any) -> list[dict[str, Any]]:
        self._sub(sub_id)
        return self.ctx.store.find("notes", subscription_id=int(sub_id))

    # -- change --------------------------------------------------------------

    def _applied(self, sub: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
        """The head snapshot with the draft's changes applied (no side effects)."""
        head = copy.deepcopy(sub)
        if "customer_id" in changes:
            customer = self.customers.require_usable(changes["customer_id"])
            head["customer_id"] = customer["id"]
        self._apply_content(head, changes)
        self._check_delivery(head)
        self._reprice(head, changes.get("monthly_net"))
        return head

    def _confirm_change(self, draft: dict[str, Any]) -> dict[str, Any]:
        sub = self._sub(draft["subscription_id"])
        changes = draft["changes"]
        if "ownership_acceptance" in self._steps(draft, sub) and not draft["ownership_accepted"]:
            raise Invalid(MESSAGES["acceptance_open"])
        head = self._applied(sub, changes)
        effective = parse_date(draft["effective_date"], "effective_date")
        if "payment_method" in changes and head["payment_method"].get("account"):
            customer = self.ctx.store.get("customers", head["customer_id"]) or {}
            head["account_check"] = self._check_account(
                head["payment_method"]["account"], customer.get("display_name") or ""
            )
        conv = self.ctx.settings["day_count"]
        live = [
            t
            for t in self.ledger.transactions(sub["id"], live_only=True)
            if t["kind"] in ("NEW", "RENEW")
        ]
        if "start_date" in changes:
            if any(t["state"] == "BOOKED" for t in live):
                raise Invalid(MESSAGES["start_locked"], {"start_date": MESSAGES["start_locked"]})
            start = parse_start(changes["start_date"])
            if self.ctx.fault("effective_date_day_month_swapped") and start.day <= 12:
                start = date(start.year, start.day, start.month)
            head["start_date"] = head["term_start"] = iso(start)
            head["term_end"] = iso(add_months(start, 12))
            for tx in live:
                self.ledger.void(tx)
            live = []
            effective = start
        head["version"] = sub["version"] + 1
        old_period = Decimal(str(sub["price"]["period_net"]))
        new_period = Decimal(str(head["price"]["period_net"]))
        rate = Decimal(str(head["price"]["tax_rate"]))
        delta = new_period - old_period
        change_net = Decimal(0)
        change_end = None
        months = FREQUENCY_MONTHS[sub["frequency"]]
        for tx in live:
            p_start = date.fromisoformat(tx["period_start"])
            p_end = date.fromisoformat(tx["period_end"])
            span = overlap(p_start, p_end, effective, date.max)
            if span is None:
                continue
            containing = p_start <= effective < p_end
            if tx["state"] == "BOOKED" or containing:
                nominal_end = add_months(p_start, months)
                change_net += self._prorate(delta, span[0], span[1], p_start, nominal_end, conv)
                change_end = max(change_end or p_end, p_end)
            else:
                share = pro_rata(
                    new_period, p_start, p_end, p_start, add_months(p_start, months), conv
                )
                tx.update(
                    net=num(share), tax=num(money(share * rate / 100)), version=head["version"]
                )
                tx["total"] = num(
                    Decimal(str(tx["net"]))
                    + Decimal(str(tx["tax"]))
                    + Decimal(str(tx["fee_net"]))
                    + Decimal(str(tx["fee_tax"]))
                )
                self.ctx.store.save("transactions", tx)
        head["status"] = sub.get("prior_status") or "active"
        head["open_draft_id"] = None
        self.ctx.store.save("subscriptions", head)
        if "start_date" in changes:
            plan = self.ctx.store.get("plans", head["plan_id"]) or {}
            self.ledger.schedule(
                head,
                "NEW",
                date.fromisoformat(head["term_start"]),
                date.fromisoformat(head["term_end"]),
                setup_fee=Decimal(str(plan.get("setup_fee", 0))),
            )
            change_end = add_months(effective, months)
        tx_start = effective
        tx_end = change_end or add_months(effective, months)
        if "start_date" in changes or tx_end <= tx_start:
            tx_end = add_months(tx_start, months)
        change_tx = self.ledger.add(
            head,
            "CHANGE",
            tx_start,
            tx_end,
            change_net,
            money(change_net * rate / 100),
            draft_id=draft["id"],
        )
        self.ledger.register(head["id"])
        self._write_version(head, iso(effective), "change")
        shown = sub if self.ctx.fault("document_renders_previous_version") else head
        self.docs.issue(shown, "plan_update_notice", {"change": change_tx})
        return self.view(head["id"], head["version"])

    def _prorate(self, delta, start, end, p_start, p_end, conv) -> Decimal:
        if self.ctx.fault("proration_floors_to_zero"):
            whole_periods = (end - start).days // max((p_end - p_start).days, 1)
            return money(delta * whole_periods)
        return pro_rata(delta, start, end, p_start, p_end, conv)

    def _check_account(self, account: str, name: str) -> dict[str, Any]:
        """Name check stub: answers MATCH, or NO_MATCH once per account listed in the setting."""
        mismatch_once = self.ctx.settings.get("account_check_mismatch_once") or []
        seen = self.ctx.counters.get(f"account-check:{account}", 0)
        self.ctx.counters[f"account-check:{account}"] = seen + 1
        result = "NO_MATCH" if account in mismatch_once and seen == 0 else "MATCH"
        return {"result": result, "name": name, "checked_at": self.ctx.stamp()}

    def check_account(self, body: dict[str, Any]) -> dict[str, Any]:
        account = str(body.get("account") or "").replace(" ", "").upper()
        message = validate_iban(account)
        if message:
            raise Invalid(message, {"account": message})
        return {"account": account, **self._check_account(account, body.get("name") or "")}

    # -- closure -------------------------------------------------------------

    def credit_preview(self, sub: dict[str, Any], effective: date) -> tuple[Decimal, Decimal]:
        """The unearned (net, tax) of booked periods after ``effective`` under the day count."""
        net, tax, _ = self._unearned(sub, effective)
        return net, tax

    def _unearned(self, sub: dict[str, Any], effective: date) -> tuple[Decimal, Decimal, Decimal]:
        """Rounded net and tax legs, and the unrounded sum of both."""
        conv = self.ctx.settings["day_count"]
        rate = Decimal(str(sub["price"]["tax_rate"]))
        net = tax = raw = Decimal(0)
        for tx in self.ledger.transactions(sub["id"], live_only=True):
            if tx["state"] != "BOOKED" or tx["kind"] == "CANCEL":
                continue
            p_start = date.fromisoformat(tx["period_start"])
            p_end = date.fromisoformat(tx["period_end"])
            span = overlap(p_start, p_end, effective, date.max)
            if span is None:
                continue
            exact_net = share_of(Decimal(str(tx["net"])), span[0], span[1], p_start, p_end, conv)
            exact_tax = share_of(Decimal(str(tx["tax"])), span[0], span[1], p_start, p_end, conv)
            if self.ctx.fault("refund_tax_applied_twice"):
                exact_tax = (exact_net + exact_tax) * rate / 100
            net += money(exact_net)
            tax += money(exact_tax)
            raw += exact_net + exact_tax
        return net, tax, raw

    def _confirm_cancel(self, draft: dict[str, Any]) -> dict[str, Any]:
        sub = self._sub(draft["subscription_id"])
        if draft.get("reason") not in CLOSURE_REASONS:
            raise Invalid(MESSAGES["reason_invalid"], {"reason": MESSAGES["reason_invalid"]})
        effective = parse_date(draft["effective_date"], "effective_date")
        net, tax, raw = self._unearned(sub, effective)
        total = net + tax
        if self.ctx.fault("refund_legs_rounded_independently"):
            total = money(raw)
        customer = self.ctx.store.get("customers", sub["customer_id"]) or {}
        payee = (sub.get("payment_method") or {}).get("account") or customer.get("payout_account")
        if (net or tax) and not payee:
            raise Invalid(MESSAGES["no_payee"], {"payout_account": MESSAGES["no_payee"]})
        conv = self.ctx.settings["day_count"]
        for tx in self.ledger.transactions(sub["id"], live_only=True):
            if tx["state"] == "BOOKED":
                continue
            p_start = date.fromisoformat(tx["period_start"])
            p_end = date.fromisoformat(tx["period_end"])
            if p_start >= effective:
                self.ledger.void(tx)
            elif p_end > effective:
                earned = pro_rata(Decimal(str(tx["net"])), p_start, effective, p_start, p_end, conv)
                rate = Decimal(str(sub["price"]["tax_rate"]))
                tx.update(
                    net=num(earned), tax=num(money(earned * rate / 100)), period_end=iso(effective)
                )
                tx["total"] = num(
                    Decimal(str(tx["net"]))
                    + Decimal(str(tx["tax"]))
                    + Decimal(str(tx["fee_net"]))
                    + Decimal(str(tx["fee_tax"]))
                )
                self.ctx.store.save("transactions", tx)
        end = max(date.fromisoformat(sub["term_end"]), effective)
        if end == effective:
            end = add_months(effective, 1)
        sub.update(
            status="cancelled",
            open_draft_id=None,
            version=sub["version"] + 1,
            closure={
                "reason": draft["reason"],
                "effective_date": iso(effective),
                "credit_net": num(-net),
                "credit_tax": num(-tax),
                "credit_total": num(-total),
                "payee_account": payee if (net or tax) else None,
            },
        )
        self.ctx.store.save("subscriptions", sub)
        cancel_tx = self.ledger.add(sub, "CANCEL", effective, end, -net, -tax, draft_id=draft["id"])
        if total != net + tax:
            cancel_tx["total"] = num(-total)
            self.ctx.store.save("transactions", cancel_tx)
        self.ledger.register(sub["id"])
        self._write_version(sub, iso(effective), "cancel")
        self.docs.issue(sub, "closure_notice", {"cancel": cancel_tx})
        return self.view(sub["id"], sub["version"])

    # -- renew ---------------------------------------------------------------

    def _renew_preview(self, sub: dict[str, Any], draft: dict[str, Any]) -> dict[str, Any]:
        pct = draft.get("index_percent")
        pct = self.ctx.settings["index_percent"] if pct in (None, "") else pct
        if self.ctx.fault("index_percent_truncated"):
            pct = int(float(pct))
        current = Decimal(str(sub["price"]["monthly_net"]))
        new = index_price(current, pct)
        old_end = date.fromisoformat(sub["term_end"])
        return {
            "index_percent": float(pct),
            "old_term_end": iso(old_end),
            "new_term_start": iso(old_end),
            "new_term_end": iso(next_term_end(old_end)),
            "current_monthly_net": num(current),
            "new_monthly_net": num(new),
        }

    def _confirm_renew(self, draft: dict[str, Any]) -> dict[str, Any]:
        sub = self._sub(draft["subscription_id"])
        preview = self._renew_preview(sub, draft)
        plan = self.ctx.store.get("plans", sub["plan_id"]) or {}
        base = monthly_net(plan)
        sub.update(
            status="renewed",
            open_draft_id=None,
            version=sub["version"] + 1,
            term_start=preview["new_term_start"],
            term_end=preview["new_term_end"],
            price_adjustment=num(Decimal(str(preview["new_monthly_net"])) - base),
        )
        self._reprice(sub)
        self.ctx.store.save("subscriptions", sub)
        self.ledger.schedule(
            sub, "RENEW", date.fromisoformat(sub["term_start"]), date.fromisoformat(sub["term_end"])
        )
        self._write_version(sub, sub["term_start"], "renew")
        self.docs.issue(sub, "term_extension_notice")
        return self.view(sub["id"], sub["version"])

    def run_renewals(self) -> dict[str, Any]:
        """The renewal job: renew every due, fully booked subscription without an open draft."""
        renewed, skipped = [], []
        for sub in self.ctx.store.find("subscriptions"):
            if sub["status"] not in ("active", "renewed") or sub.get("open_draft_id"):
                continue
            try:
                self._check_renewable(sub)
            except Invalid as exc:
                skipped.append({"id": sub["id"], "reason": exc.message})
                continue
            draft = self.open_draft(sub["id"], {"kind": "renew"})
            self.confirm_draft(draft["id"])
            renewed.append(sub["id"])
        return {"renewed": renewed, "skipped": skipped}


# -- keyset search ------------------------------------------------------------

SEARCHABLE = {
    "subscriptions": "subscriptions",
    "customers": "customers",
    "partners": "partners",
    "plans": "plans",
}


def _title(ctx: Context, resource: str, doc: dict[str, Any]) -> str:
    if resource == "subscriptions":
        customer = ctx.store.get("customers", doc.get("customer_id")) or {}
        return f"{doc.get('number', '')} {customer.get('display_name', '')}".strip()
    if resource == "plans":
        return f"{doc.get('code', '')} {doc.get('name', '')}"
    if resource == "customers":
        return f"{doc.get('number', '')} {doc.get('display_name', '')}".strip()
    return f"{doc.get('number', '')} {doc.get('name', '')}".strip()


def encode_cursor(doc_id: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"id": doc_id}).encode()).decode()


def decode_cursor(cursor: str) -> int:
    try:
        return int(json.loads(base64.urlsafe_b64decode(cursor.encode()))["id"])
    except Exception as exc:  # any malformed cursor is the caller's error
        raise BadRequest("cursor is not valid") from exc


def search(ctx: Context, body: dict[str, Any]) -> dict[str, Any]:
    """Keyset-paged search: ``after``/``before`` cursors over ascending ids, never offsets."""
    resource = body.get("resource", "subscriptions")
    if resource not in SEARCHABLE:
        raise BadRequest(f"resource must be one of {sorted(SEARCHABLE)}")
    limit = max(1, min(int(body.get("limit", 10)), 100))
    text = str(body.get("text") or "").strip().lower()
    rows = ctx.store.find(SEARCHABLE[resource])
    items = []
    for row in rows:
        title = _title(ctx, resource, row)
        if text and text not in title.lower():
            continue
        item = {**row, "title": title}
        if resource == "customers":
            item["name"] = row.get("display_name")
        items.append(item)
    if body.get("before"):
        edge = decode_cursor(body["before"])
        page = [i for i in items if i["id"] < edge][-limit:]
    else:
        edge = decode_cursor(body["after"]) if body.get("after") else 0
        page = [i for i in items if i["id"] > edge][:limit]
    has_next = bool(page) and any(i["id"] > page[-1]["id"] for i in items)
    has_prev = bool(page) and any(i["id"] < page[0]["id"] for i in items)
    return {
        "resource": resource,
        "items": page,
        "next_cursor": encode_cursor(page[-1]["id"]) if has_next else None,
        "prev_cursor": encode_cursor(page[0]["id"]) if has_prev else None,
        "total": len(items),
    }
