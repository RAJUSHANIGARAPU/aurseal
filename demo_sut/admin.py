"""Reference data and administration: plans, partners (resellers) and their reseller terms,
ledger accounts, settlements, users and roles, integrations, exports, tasks, audit trail,
tokens and seeds."""

from __future__ import annotations

import io
import ipaddress
import json
import secrets
import zipfile
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from demo_sut.clock import normalize_instant
from demo_sut.core import BadRequest, Conflict, Context, Invalid, NotFound, Unauthorized, num
from demo_sut.customers import IdSequences
from demo_sut.documents import DocumentRenderer

# -- seeds ---------------------------------------------------------------------

SEED_PLANS = [
    {
        "code": "TEAM-STD",
        "name": "Team plan",
        "kind": "TEAM",
        "base_monthly_net": 32.5,
        "tax_rate": 20.0,
        "setup_fee": 20.0,
    },
    {
        "code": "SOLO-STD",
        "name": "Individual plan",
        "kind": "INDIVIDUAL",
        "base_monthly_net": 9.75,
        "tax_rate": 20.0,
        "setup_fee": 4.0,
    },
]
SEED_ACCOUNTS = [
    {"number": "1300", "name": "Receivables", "managed": True, "role": "RECEIVABLE"},
    {"number": "1500", "name": "Tax payable", "managed": True, "role": "TAX"},
    {"number": "8000", "name": "Subscription revenue", "managed": True, "role": "REVENUE"},
    {"number": "8100", "name": "Fee income", "managed": True, "role": "FEES"},
    {"number": "4000", "name": "Sundry costs", "managed": False, "role": None},
    {"number": "4100", "name": "Marketing", "managed": False, "role": None},
]
SEED_USERS = [
    {"username": "demo-admin", "password": "demo-password", "roles": ["ADMIN", "CLERK"]},
    {"username": "demo-clerk", "password": "demo-password", "roles": ["CLERK"]},
]
DEMO_CLIENTS = {"demo-client": "demo-client-secret"}
ROLES = ("ADMIN", "CLERK", "AUDITOR")
INTEGRATION_SERVICES = [
    {
        "key": "mailer",
        "name": "Mail delivery",
        "params": ["apiKey"],
        "optionalParams": ["baseUrl"],
    },
    {
        "key": "storage",
        "name": "File storage",
        "params": ["apiKey"],
        "optionalParams": ["baseUrl", "timeoutSeconds"],
    },
]
ACCOUNT_MESSAGES = {"managed": "Account is managed by the system and cannot be renamed."}


def seed(ctx: Context) -> None:
    """Fill an empty database with the demo's reference data (no customers, no subscriptions)."""
    store = ctx.store
    terms = store.insert(
        "reseller_terms", {"name": "Standard terms", "share_percent": 10.0, "flat_fee": 0.0}
    )
    partner = store.insert(
        "partners", {"name": "Seed Channel Partner", "reseller_terms_id": terms["id"]}
    )
    partner["number"] = IdSequences.issue("PAR", partner["id"])
    store.save("partners", partner)
    for plan in SEED_PLANS:
        store.insert("plans", dict(plan))
    for account in SEED_ACCOUNTS:
        store.insert("accounts", dict(account))
    for user in SEED_USERS:
        saved = store.insert("users", {**user, "roles": list(user["roles"])})
        store.insert(
            "role_events",
            {
                "user_id": saved["id"],
                "type": "ASSIGNED",
                "roles": list(user["roles"]),
                "at": ctx.stamp(),
            },
        )


# -- reference data ---------------------------------------------------------------


class ReferenceData:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx

    def _get(self, table: str, name: str, ident: Any) -> dict[str, Any]:
        doc = self.ctx.store.get(table, ident)
        if doc is None:
            raise NotFound(name, ident)
        return doc

    # reseller terms
    def create_terms(self, body: dict[str, Any]) -> dict[str, Any]:
        errors: dict[str, str] = {}
        if not str(body.get("name") or "").strip():
            errors["name"] = "Name is required."
        cap = Decimal(str(self.ctx.settings["commission_cap_percent"]))
        share = body.get("share_percent")
        try:
            share_value = Decimal(str(share))
        except Exception:  # noqa: BLE001 - anything unparseable is a validation error
            share_value = None
        if share is None or isinstance(share, bool) or share_value is None:
            errors["share_percent"] = "Share percent is required."
        elif not Decimal(0) <= share_value <= cap:
            errors["share_percent"] = f"Share percent must be between 0 and {cap}."
        try:
            fee = Decimal(str(body.get("flat_fee", 0) or 0))
        except Exception:  # noqa: BLE001
            fee = Decimal(-1)
        if fee < 0:
            errors["flat_fee"] = "Flat fee must not be negative."
        if errors:
            raise Invalid(next(iter(errors.values())), errors)
        stored_share = share_value
        if self.ctx.fault("reseller_share_scaled_twice"):
            stored_share = share_value / 100
        return self.ctx.store.insert(
            "reseller_terms",
            {
                "name": body["name"],
                "share_percent": float(stored_share),
                "flat_fee": num(fee),
                "run_tag": self.ctx.run_tag,
            },
        )

    def terms(self, ident: Any) -> dict[str, Any]:
        return self._get("reseller_terms", "reseller terms", ident)

    # partners
    def create_partner(self, body: dict[str, Any]) -> dict[str, Any]:
        if not str(body.get("name") or "").strip():
            raise Invalid("Name is required.", {"name": "Name is required."})
        if body.get("reseller_terms_id") is not None:
            self.terms(body["reseller_terms_id"])
        doc = self.ctx.store.insert(
            "partners",
            {
                "name": body["name"],
                "reseller_terms_id": body.get("reseller_terms_id"),
                "run_tag": self.ctx.run_tag,
            },
        )
        doc["number"] = IdSequences.issue("PAR", doc["id"])
        return self.ctx.store.save("partners", doc)

    def partner(self, ident: Any) -> dict[str, Any]:
        return self._get("partners", "partner", ident)

    # plans
    def create_plan(self, body: dict[str, Any]) -> dict[str, Any]:
        errors = {}
        if body.get("kind") not in ("TEAM", "INDIVIDUAL"):
            errors["kind"] = "Plan kind must be TEAM or INDIVIDUAL."
        if not str(body.get("code") or "").strip():
            errors["code"] = "Code is required."
        if errors:
            raise Invalid(next(iter(errors.values())), errors)
        return self.ctx.store.insert(
            "plans",
            {
                "code": body["code"],
                "name": body.get("name") or body["code"],
                "kind": body["kind"],
                "base_monthly_net": float(body.get("base_monthly_net", 10)),
                "tax_rate": float(body.get("tax_rate", 20)),
                "setup_fee": float(body.get("setup_fee", 0)),
                "run_tag": self.ctx.run_tag,
            },
        )

    def plan(self, ident: Any) -> dict[str, Any]:
        return self._get("plans", "plan", ident)


# -- ledger accounts and settlements ----------------------------------------------


class AccountsService:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx

    def list(self) -> list[dict[str, Any]]:
        return [self.public(a) for a in self.ctx.store.find("accounts")]

    @staticmethod
    def public(account: dict[str, Any]) -> dict[str, Any]:
        return {k: account.get(k) for k in ("id", "number", "name", "managed", "role")}

    def get(self, ident: Any) -> dict[str, Any]:
        doc = self.ctx.store.get("accounts", ident)
        if doc is None:
            raise NotFound("account", ident)
        return doc

    def rename(self, ident: Any, body: dict[str, Any]) -> dict[str, Any]:
        doc = self.get(ident)
        if doc["managed"]:
            raise Conflict(ACCOUNT_MESSAGES["managed"])
        name = str(body.get("name") or "").strip()
        if not name:
            raise Invalid("Name is required.", {"name": "Name is required."})
        doc["name"] = name
        return self.public(self.ctx.store.save("accounts", doc))

    def mappings(self) -> dict[str, str]:
        return {a["role"]: a["number"] for a in self.ctx.store.find("accounts") if a.get("role")}


class Settlements:
    """Reseller settlements and their statement export.

    For every booked transaction of a reseller's subscriptions in the period, the reseller
    earns ``net x share_percent / 100`` plus the terms' ``flat_fee``.
    """

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx

    def create(self, body: dict[str, Any]) -> dict[str, Any]:
        partner = self.ctx.store.get("partners", body.get("partner_id"))
        if partner is None:
            raise Invalid("Unknown partner.", {"partner_id": "Unknown partner."})
        start, end = body.get("period_start", "0000-01-01"), body.get("period_end", "9999-12-31")
        terms = self.ctx.store.get("reseller_terms", partner.get("reseller_terms_id")) or {}
        share = Decimal(str(terms.get("share_percent") or 0))
        flat = Decimal(str(terms.get("flat_fee") or 0))
        total = Decimal(0)
        for sub in self.ctx.store.find("subscriptions", partner_id=partner["id"]):
            for tx in self.ctx.store.find("transactions", subscription_id=sub["id"]):
                if tx["state"] == "BOOKED" and start <= tx["period_start"] < end:
                    total += Decimal(str(tx["net"])) * share / 100 + flat
        doc = self.ctx.store.insert(
            "settlements",
            {
                "partner_id": partner["id"],
                "partner_number": partner["number"],
                "partner_name": partner["name"],
                "period_start": start,
                "period_end": end,
                "status": "open",
                "closing_balance": num(total),
                "created_at": self.ctx.stamp(),
            },
        )
        doc["reference"] = f"SET-{doc['id']:05d}"
        return self.ctx.store.save("settlements", doc)

    def list(self, party: Any = None, tab: str | None = None) -> list[dict[str, Any]]:
        rows = self.ctx.store.find("settlements")
        if party not in (None, ""):
            rows = [r for r in rows if str(r["partner_id"]) == str(party)]
        if tab:
            rows = [r for r in rows if r["status"] == tab]
        return rows

    def get(self, ident: Any) -> dict[str, Any]:
        doc = self.ctx.store.get("settlements", ident)
        if doc is None:
            raise NotFound("settlement", ident)
        return doc

    def close(self, ident: Any) -> dict[str, Any]:
        doc = self.get(ident)
        doc["status"] = "closed"
        return self.ctx.store.save("settlements", doc)

    def statement(self, ident: Any) -> dict[str, Any]:
        """Exactly one entry: |closing balance|, CREDIT when positive, DEBIT when negative."""
        doc = self.get(ident)
        balance = Decimal(str(doc["closing_balance"]))
        return {
            "settlement": doc["reference"],
            "entries": [
                {
                    "reference": doc["reference"],
                    "amount": num(abs(balance)),
                    "side": "CREDIT" if balance >= 0 else "DEBIT",
                    "party": doc["partner_number"],
                }
            ],
        }


# -- users, roles, tokens ----------------------------------------------------------


class UsersService:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx

    @staticmethod
    def public(user: dict[str, Any]) -> dict[str, Any]:
        return {k: user[k] for k in ("id", "username", "roles")}

    def list(self) -> list[dict[str, Any]]:
        return [self.public(u) for u in self.ctx.store.find("users")]

    def get(self, ident: Any) -> dict[str, Any]:
        doc = self.ctx.store.get("users", ident)
        if doc is None:
            raise NotFound("user", ident)
        return doc

    def change_roles(self, ident: Any, body: dict[str, Any]) -> dict[str, Any]:
        user = self.get(ident)
        assign = [r for r in body.get("assign") or [] if r not in user["roles"]]
        revoke = [r for r in body.get("revoke") or [] if r in user["roles"]]
        unknown = sorted(set(assign + revoke) - set(ROLES))
        if unknown:
            raise Invalid(f"Unknown roles: {', '.join(unknown)}.")
        for kind, roles in (("ASSIGNED", assign), ("REVOKED", revoke)):
            if roles:
                self.ctx.store.insert(
                    "role_events",
                    {"user_id": user["id"], "type": kind, "roles": roles, "at": self.ctx.stamp()},
                )
        user["roles"] = [r for r in user["roles"] if r not in revoke] + assign
        return self.public(self.ctx.store.save("users", user))

    def role_history(self, ident: Any, since: str | None = None, until: str | None = None):
        self.get(ident)
        low = normalize_instant(since) if since else None
        high = normalize_instant(until) if until else None
        rows = self.ctx.store.find("role_events", user_id=int(ident))
        rows = [r for r in rows if (not low or r["at"] >= low) and (not high or r["at"] < high)]
        if self.ctx.fault("role_history_sorted_as_text"):
            return sorted(rows, key=lambda r: str(r["id"]))
        return sorted(rows, key=lambda r: (r["at"], r["id"]))

    # tokens ---------------------------------------------------------------------

    def issue_token(self, form: dict[str, str]) -> dict[str, Any]:
        grant = form.get("grant_type")
        if grant == "password":
            users = self.ctx.store.find("users", username=form.get("username", ""))
            if not users or users[0]["password"] != form.get("password"):
                raise Unauthorized("invalid username or password", error_code="invalid_grant")
            subject = users[0]["username"]
        elif grant == "client_credentials":
            client = form.get("client_id", "")
            if DEMO_CLIENTS.get(client) != form.get("client_secret"):
                raise Unauthorized("invalid client", error_code="invalid_client")
            subject = client
        else:
            raise BadRequest("unsupported grant_type", error_code="unsupported_grant_type")
        token = secrets.token_urlsafe(24)
        self.ctx.store.insert(
            "tokens",
            {"token": token, "subject": subject, "grant": grant, "issued_at": self.ctx.stamp()},
        )
        return {"access_token": token, "token_type": "Bearer", "expires_in": 3600}

    def subject_for(self, token: str | None) -> str | None:
        if not token:
            return None
        rows = self.ctx.store.find("tokens", token=token)
        return rows[0]["subject"] if rows else None

    def login(self, username: str, password: str) -> str | None:
        users = self.ctx.store.find("users", username=username)
        if not users or users[0]["password"] != password:
            return None
        token = secrets.token_urlsafe(24)
        self.ctx.store.insert(
            "tokens",
            {
                "token": token,
                "subject": username,
                "grant": "session",
                "issued_at": self.ctx.stamp(),
            },
        )
        return token


# -- integrations -------------------------------------------------------------------


def base_url_problem(url: str) -> str | None:
    """Why a base URL is refused (``None`` when acceptable): https, public host only."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "Base URL is not a valid URL."
    if parts.scheme != "https":
        return "Base URL must use https."
    host = (parts.hostname or "").lower()
    if not host:
        return "Base URL must name a host."
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".internal"):
        return "Base URL must not point at a private or loopback host."
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or (address.is_reserved or address.is_unspecified)
    ):
        return "Base URL must not point at a private or loopback host."
    return None


class IntegrationsService:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx

    def services(self) -> list[dict[str, Any]]:
        return [dict(s) for s in INTEGRATION_SERVICES]

    def register(self, service: str, body: dict[str, Any]) -> dict[str, Any]:
        known = {s["key"]: s for s in INTEGRATION_SERVICES}
        if service not in known:
            raise NotFound("integration service", service)
        missing = [p for p in known[service]["params"] if not body.get(p)]
        if missing:
            raise BadRequest(f"missing parameters: {', '.join(missing)}")
        if body.get("baseUrl"):
            problem = base_url_problem(str(body["baseUrl"]))
            if problem:
                raise BadRequest(problem, field="baseUrl")
        doc = {
            "service": service,
            "baseUrl": body.get("baseUrl"),
            "configured_at": self.ctx.stamp(),
        }
        existing = self.ctx.store.find("integrations", service=service)
        if existing:
            doc["id"] = existing[0]["id"]
            return self.ctx.store.save("integrations", doc)
        return self.ctx.store.insert("integrations", doc)

    def list(self) -> list[dict[str, Any]]:
        return self.ctx.store.find("integrations")


# -- exports, tasks, audit -------------------------------------------------------------


class ExportsService:
    def __init__(self, ctx: Context, docs: DocumentRenderer) -> None:
        self.ctx = ctx
        self.docs = docs

    def create(self, body: dict[str, Any]) -> dict[str, Any]:
        kind = body.get("kind", "audit")
        if kind not in ("audit", "subscriptions"):
            raise Invalid("Export kind must be audit or subscriptions.")
        rows = self.ctx.store.find("audit" if kind == "audit" else "subscriptions")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(f"{kind}.json", json.dumps(rows, indent=2))
        stamp = self.ctx.stamp()
        file = self.docs.store_file(f"export-{kind}.zip", "application/zip", buffer.getvalue())
        return self.ctx.store.insert(
            "exports",
            {
                "kind": kind,
                "status": "DONE",
                "attemptCount": 1,
                "lastAttemptAt": stamp,
                "file_id": file["id"],
                "rows": len(rows),
                "created_at": stamp,
            },
        )

    def list(self) -> list[dict[str, Any]]:
        return self.ctx.store.find("exports")

    def get(self, ident: Any) -> dict[str, Any]:
        doc = self.ctx.store.get("exports", ident)
        if doc is None:
            raise NotFound("export", ident)
        return doc


class TasksService:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx

    def create(self, body: dict[str, Any]) -> dict[str, Any]:
        title = body.get("title")
        if self.ctx.fault("task_title_presence_only"):
            missing = title is None
        else:
            missing = not str(title or "").strip()
        if missing:
            raise Invalid("Title is required.", {"title": "Title is required."})
        sub = None
        if body.get("subscription_id") is not None:
            sub = self.ctx.store.get("subscriptions", body["subscription_id"])
            if sub is None:
                raise Invalid("Unknown subscription.", {"subscription_id": "Unknown subscription."})
        target = body.get("target_at") or self.ctx.stamp()
        try:
            moment = datetime.fromisoformat(str(target).replace("Z", "+00:00"))
        except ValueError as exc:
            raise Invalid("target_at must be an ISO date-time.") from exc
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return self.ctx.store.insert(
            "tasks",
            {
                "title": body["title"],
                "subscription_id": sub["id"] if sub else None,
                "subscription_number": sub["number"] if sub else None,
                "version": sub["version"] if sub else None,
                "target_at": moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "state": "OPEN",
                "created_at": self.ctx.stamp(),
                "run_tag": self.ctx.run_tag,
            },
        )

    def list(self) -> list[dict[str, Any]]:
        return self.ctx.store.find("tasks")

    def complete(self, ident: Any) -> dict[str, Any]:
        doc = self.ctx.store.get("tasks", ident)
        if doc is None:
            raise NotFound("task", ident)
        doc["state"] = "DONE"
        return self.ctx.store.save("tasks", doc)


def record_audit(ctx: Context, method: str, path: str, status: int, entity: tuple | None):
    entity_type, entity_id = entity or (None, None)
    ctx.store.insert(
        "audit",
        {
            "at": ctx.stamp(),
            "actor": ctx.actor,
            "method": method,
            "path": path,
            "status": status,
            "entity_type": entity_type,
            "entity_id": None if entity_id is None else str(entity_id),
            "run_tag": ctx.run_tag,
        },
    )
