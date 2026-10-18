"""Prices, the transaction ledger, invoices and journal entries.

Amounts are :class:`~decimal.Decimal` internally and plain JSON numbers (2 decimals) on the
wire. A subscription is billed in periods (monthly, quarterly or yearly). Each period is
one ledger *transaction*; booking a transaction issues an invoice and a balanced journal
entry against the accounts mapped to the roles RECEIVABLE, REVENUE, TAX and FEES.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from demo_sut.core import (
    FREQUENCY_MONTHS,
    Context,
    Invalid,
    NotFound,
    add_months,
    day_span,
    iso,
    money,
    num,
    parse_date,
)

#: Add-ons a subscription can switch on: key -> screen label. They do not change the price.
ADDONS: dict[str, str] = {
    "sso": "Single sign-on",
    "audit_trail": "Audit trail",
    "sandbox": "Sandbox workspace",
}

MIN_AMOUNT = Decimal("1.00")
MAX_AMOUNT = Decimal("10000.00")

PRICE_MESSAGES = {
    "negative": "Amount must not be negative.",
    "minimum": "Amount must be at least EUR 1.00.",
    "maximum": "Amount must be at most EUR 10000.00.",
    "tax_rate": "Tax rate must be between 0 and 100.",
    "commission_cap": "Commission may not exceed {cap}%.",
}

#: Account roles used by journal entries.
ROLES = ("RECEIVABLE", "REVENUE", "TAX", "FEES")


def price_quote(ctx: Context, body: dict[str, Any]) -> dict[str, Any]:
    """Validate and compute a price: net, tax, gross and the partner commission.

    Rules: the net amount must be within EUR 1.00 .. 10000.00 and not negative; the tax rate
    within 0..100; the commission at most the configured cap. Amounts round half-up.
    """
    truncate = ctx.fault("rounding_drift")
    errors: dict[str, str] = {}
    net = money(body.get("net_amount", 0), truncate=truncate)
    if net < 0 and not ctx.fault("negative_price_accepted"):
        errors["net_amount"] = PRICE_MESSAGES["negative"]
    elif Decimal(0) <= net < MIN_AMOUNT:
        errors["net_amount"] = PRICE_MESSAGES["minimum"]
    elif net > MAX_AMOUNT:
        errors["net_amount"] = PRICE_MESSAGES["maximum"]
    rate = Decimal(str(body.get("tax_rate", 20)))
    if not Decimal(0) <= rate <= Decimal(100):
        errors["tax_rate"] = PRICE_MESSAGES["tax_rate"]
    pct_raw = body.get("commission_percentage")
    pct = Decimal(str(pct_raw)) if pct_raw is not None else Decimal(0)
    cap = Decimal(str(ctx.settings["commission_cap_percent"]))
    if pct > cap or pct < 0:
        errors["commission_percentage"] = PRICE_MESSAGES["commission_cap"].format(cap=cap)
    if errors:
        raise Invalid(next(iter(errors.values())), errors)
    tax = money(net * rate / 100, truncate=truncate)
    return {
        "net_amount": num(net),
        "tax_rate": float(rate),
        "tax_amount": num(tax),
        "gross_amount": num(net + tax),
        "commission_percentage": float(pct),
        "commission_amount": num(money(net * pct / 100, truncate=truncate)),
    }


def monthly_net(plan: dict[str, Any], adjustment: Any = 0) -> Decimal:
    """The plan's base price plus any agreed adjustment (add-ons are free)."""
    return Decimal(str(plan["base_monthly_net"])) + Decimal(str(adjustment or 0))


def price_block(monthly: Decimal, tax_rate: Any, frequency: str) -> dict[str, Any]:
    rate = Decimal(str(tax_rate))
    months = FREQUENCY_MONTHS[frequency]
    m_tax = money(monthly * rate / 100)
    p_net = monthly * months
    p_tax = money(p_net * rate / 100)
    return {
        "monthly_net": num(monthly),
        "tax_rate": float(rate),
        "monthly_tax": num(m_tax),
        "monthly_gross": num(monthly + m_tax),
        "period_net": num(p_net),
        "period_tax": num(p_tax),
        "period_gross": num(p_net + p_tax),
    }


def index_price(monthly: Decimal, percent: Any) -> Decimal:
    """The renewed monthly price: ``monthly x (1 + percent / 100)``, half-up to the cent."""
    return money(monthly * (1 + Decimal(str(percent)) / 100))


START_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(T[\d:.]+(Z|[+-]\d{2}:\d{2})?)?$")


def parse_start(value: Any) -> date:
    """A start date, given as an ISO date or an ISO date-time (the time part is ignored)."""
    match = START_RE.match(str(value or ""))
    if not match:
        raise Invalid(
            "Start must be an ISO date or date-time.", {"start_date": "Must be an ISO date."}
        )
    return parse_date(match.group(1), "start_date")


def share_of(amount: Decimal, start: date, end: date, p_start: date, p_end: date, conv: str):
    """``amount`` scaled by the share of [p_start, p_end) covered by [start, end), unrounded."""
    whole = day_span(p_start, p_end, conv)
    part = day_span(start, end, conv)
    if whole <= 0 or part <= 0:
        return Decimal(0)
    return Decimal(amount) * part / whole


def pro_rata(amount: Decimal, start: date, end: date, p_start: date, p_end: date, conv: str):
    """:func:`share_of`, rounded half-up to the cent."""
    return money(share_of(amount, start, end, p_start, p_end, conv))


def periods(start: date, end: date, frequency: str) -> list[tuple[date, date]]:
    """Billing periods from ``start`` up to ``end``; the last one is clipped at ``end``."""
    months = FREQUENCY_MONTHS[frequency]
    out: list[tuple[date, date]] = []
    current, i = start, 0
    while current < end:
        i += 1
        nxt = min(add_months(start, months * i), end)
        out.append((current, nxt))
        current = nxt
    return out


class Ledger:
    """Transactions, booking jobs, invoices and journal entries."""

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.on_booked: list[Any] = []  # callbacks(tx, invoice)

    # -- schedule ------------------------------------------------------------

    def schedule(
        self,
        sub: dict[str, Any],
        kind: str,
        start: date,
        end: date,
        *,
        setup_fee: Decimal = Decimal(0),
    ) -> list[dict[str, Any]]:
        """Create one transaction per billing period between ``start`` and ``end``."""
        price = sub["price"]
        full = Decimal(str(price["period_net"]))
        rate = Decimal(str(price["tax_rate"]))
        conv = self.ctx.settings["day_count"]
        made = []
        months = FREQUENCY_MONTHS[sub["frequency"]]
        for i, (p_start, p_end) in enumerate(periods(start, end, sub["frequency"])):
            nominal_end = add_months(p_start, months)
            net = (
                full
                if p_end == nominal_end
                else pro_rata(full, p_start, p_end, p_start, nominal_end, conv)
            )
            fee = setup_fee if i == 0 else Decimal(0)
            made.append(
                self.add(
                    sub,
                    kind,
                    p_start,
                    p_end,
                    net,
                    money(net * rate / 100),
                    fee,
                    money(fee * rate / 100),
                    origin=f"{kind.lower()}:{sub['version']}",
                )
            )
        self.register(sub["id"])
        return made

    def add(
        self,
        sub: dict[str, Any],
        kind: str,
        p_start: date,
        p_end: date,
        net: Decimal,
        tax: Decimal,
        fee_net: Decimal = Decimal(0),
        fee_tax: Decimal = Decimal(0),
        **extra: Any,
    ) -> dict[str, Any]:
        tx = {
            "subscription_id": sub["id"],
            "version": sub["version"],
            "kind": kind,
            "state": "INITIAL",
            "period_start": iso(p_start),
            "period_end": iso(p_end),
            "net": num(net),
            "tax": num(tax),
            "fee_net": num(fee_net),
            "fee_tax": num(fee_tax),
            "total": num(net + tax + fee_net + fee_tax),
            "job_id": None,
            "booked_at": None,
            "created_at": self.ctx.stamp(),
            **extra,
        }
        saved = self.ctx.store.insert("transactions", tx)
        saved["reference"] = f"TX-{saved['id']}"
        return self.ctx.store.save("transactions", saved)

    def transactions(self, subscription_id: int, *, live_only: bool = False) -> list[dict]:
        rows = self.ctx.store.find("transactions", subscription_id=subscription_id)
        rows.sort(key=lambda t: (t["period_start"], t["id"]))
        return [t for t in rows if t["state"] != "VOID"] if live_only else rows

    def get(self, tx_id: Any) -> dict[str, Any]:
        tx = self.ctx.store.get("transactions", tx_id)
        if tx is None:
            raise NotFound("transaction", tx_id)
        return tx

    def register(self, subscription_id: int) -> None:
        """Give transactions a booking job: all of them, or only the earliest (one-at-a-time)."""
        pending = [
            t for t in self.transactions(subscription_id) if t["state"] in ("INITIAL", "SCHEDULED")
        ]
        if self.ctx.settings["scheduler_mode"] == "one_at_a_time":
            if any(t["state"] == "SCHEDULED" for t in pending):
                return
            pending = pending[:1]
        for tx in pending:
            if tx["state"] == "INITIAL":
                self._attach_job(tx)

    def _attach_job(self, tx: dict[str, Any]) -> None:
        job = self.ctx.store.insert(
            "jobs",
            {
                "kind": "BOOK_TRANSACTION",
                "state": "PENDING",
                "due_date": tx["period_start"],
                "transaction_id": tx["id"],
                "subscription_id": tx["subscription_id"],
                "created_at": self.ctx.stamp(),
            },
        )
        tx.update(state="SCHEDULED", job_id=job["id"])
        self.ctx.store.save("transactions", tx)

    def void(self, tx: dict[str, Any]) -> None:
        tx["state"] = "VOID"
        self.ctx.store.save("transactions", tx)
        if tx.get("job_id"):
            job = self.ctx.store.get("jobs", tx["job_id"])
            if job and job["state"] == "PENDING":
                job["state"] = "CANCELLED"
                self.ctx.store.save("jobs", job)

    # -- booking -------------------------------------------------------------

    def book(self, tx: dict[str, Any]) -> dict[str, Any]:
        """Book one transaction: invoice, journal entry, then the on-booked hooks."""
        if tx["state"] == "BOOKED":
            return tx
        tx.update(state="BOOKED", booked_at=self.ctx.stamp())
        self.ctx.store.save("transactions", tx)
        if tx.get("job_id"):
            job = self.ctx.store.get("jobs", tx["job_id"])
            if job:
                job.update(state="DONE", finished_at=self.ctx.stamp())
                self.ctx.store.save("jobs", job)
        invoice = self._invoice(tx)
        for hook in self.on_booked:
            hook(tx, invoice)
        if self.ctx.settings["scheduler_mode"] == "one_at_a_time":
            self.register(tx["subscription_id"])
        return self.get(tx["id"])

    def rebook(self, tx_id: Any) -> dict[str, Any]:
        """Issue the booking of an already booked transaction again (only reached by a fault)."""
        tx = self.get(tx_id)
        invoice = self._invoice(tx)
        for hook in self.on_booked:
            hook(tx, invoice)
        return tx

    def _invoice(self, tx: dict[str, Any]) -> dict[str, Any]:
        today = self.ctx.clock.today()
        period_start = date.fromisoformat(tx["period_start"])
        reference = max(today, period_start)
        net = Decimal(str(tx["net"])) + Decimal(str(tx["fee_net"]))
        tax = Decimal(str(tx["tax"])) + Decimal(str(tx["fee_tax"]))
        sub = self.ctx.store.get("subscriptions", tx["subscription_id"]) or {}
        invoice = self.ctx.store.insert(
            "invoices",
            {
                "subscription_id": tx["subscription_id"],
                "transaction_id": tx["id"],
                "status": "OPEN",
                "kind": tx["kind"],
                "net": num(net),
                "tax": num(tax),
                "total": num(net + tax),
                "remaining": num(net + tax),
                "tax_rate": (sub.get("price") or {}).get("tax_rate", 0),
                "booking_date": iso(today),
                "document_date": iso(today),
                "reference_date": iso(reference),
                "due_date": iso(today + timedelta(days=self.ctx.settings["payment_term_days"])),
                "collection_date": iso(reference),
                "period_start": tx["period_start"],
                "period_end": tx["period_end"],
                "document_id": None,
            },
        )
        invoice["number"] = str(100000 + invoice["id"])
        self.ctx.store.save("invoices", invoice)
        self._journal(invoice, tx)
        return invoice

    def _journal(self, invoice: dict[str, Any], tx: dict[str, Any]) -> None:
        accounts = mapping(self.ctx)
        lines = []

        def line(role: str, amount: Decimal, debit_side: bool) -> None:
            if amount == 0:
                return
            if amount < 0:
                amount, debit_side = -amount, not debit_side
            lines.append(
                {
                    "account": accounts[role],
                    "role": role,
                    "debit": num(amount) if debit_side else 0.0,
                    "credit": 0.0 if debit_side else num(amount),
                }
            )

        line("RECEIVABLE", Decimal(str(invoice["total"])), True)
        line("REVENUE", Decimal(str(tx["net"])), False)
        line("TAX", Decimal(str(tx["tax"])) + Decimal(str(tx["fee_tax"])), False)
        line("FEES", Decimal(str(tx["fee_net"])), False)
        self.ctx.store.insert(
            "journal",
            {
                "invoice_id": invoice["id"],
                "reference": tx["reference"],
                "references": [tx["reference"], invoice["number"]],
                "booked_at": self.ctx.stamp(),
                "lines": lines,
            },
        )

    def journal(self, references: list[str] | None = None) -> list[dict[str, Any]]:
        rows = self.ctx.store.find("journal")
        if references:
            wanted = set(references)
            rows = [r for r in rows if wanted & set(r["references"])]
        return rows

    def pay(
        self, invoice_id: Any, amount: Any = None, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Record a payment. A repeated ``idempotency_key`` returns the first payment."""
        invoice = self.ctx.store.get("invoices", invoice_id)
        if invoice is None:
            raise NotFound("invoice", invoice_id)
        if idempotency_key and not self.ctx.fault("payment_replay_double_counted"):
            for earlier in self.ctx.store.find("payments", invoice_id=invoice["id"]):
                if earlier.get("idempotency_key") == idempotency_key:
                    return {**invoice, "payment": earlier, "replayed": True}
        paid = money(amount if amount is not None else invoice["remaining"])
        remaining = Decimal(str(invoice["remaining"])) - paid
        invoice.update(remaining=num(remaining), status="PAID" if remaining <= 0 else "OPEN")
        self.ctx.store.save("invoices", invoice)
        payment = self.ctx.store.insert(
            "payments",
            {
                "invoice_id": invoice["id"],
                "invoice_number": invoice["number"],
                "amount": num(paid),
                "paid_at": self.ctx.stamp(),
                "reference": f"PAY-{invoice['number']}",
                "idempotency_key": idempotency_key,
            },
        )
        return {**invoice, "payment": payment, "replayed": False}


def mapping(ctx: Context) -> dict[str, str]:
    """Account role -> account number, from the seeded accounts."""
    out = {}
    for account in ctx.store.find("accounts"):
        if account.get("role"):
            out[account["role"]] = account["number"]
    return out


def now_date(ctx: Context) -> date:
    return ctx.clock.today()


def to_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else date.fromisoformat(value[:10])
