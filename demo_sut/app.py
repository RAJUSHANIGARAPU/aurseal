"""The WSGI application: JSON API under ``/api``, a gateway under ``/gw``, screens under ``/ui``.

Every response carries:

* ``X-Demo-Faults`` - the active fault switches (``none`` when healthy);
* ``X-Demo-Clock`` - the demo clock (``<instant>; real|frozen``);
* ``X-Demo-Source`` - ``service`` (an API route answered), ``gateway`` (the gateway itself
  answered, e.g. its own no-route 404) or ``ui``.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import Any

import yaml

from demo_sut import message_attachments, ui
from demo_sut.admin import (
    AccountsService,
    ExportsService,
    IntegrationsService,
    ReferenceData,
    Settlements,
    TasksService,
    UsersService,
    record_audit,
    seed,
)
from demo_sut.billing import Ledger, mapping, monthly_net, price_quote
from demo_sut.clock import normalize_instant
from demo_sut.core import (
    BadRequest,
    Context,
    DemoError,
    NotFound,
    Throttled,
    Unauthorized,
    Unavailable,
)
from demo_sut.customers import CustomerService
from demo_sut.documents import DocumentRenderer
from demo_sut.faults import FAULTS, UnknownFault
from demo_sut.http import Request, Response, Router, bytes_response, json_response
from demo_sut.messaging import Outbox
from demo_sut.scheduler import Scheduler
from demo_sut.store import Store
from demo_sut.subscriptions import SubscriptionService, search

CONFIG_DIR = Path(__file__).parent / "config"
#: First path segment after /gw/ that the gateway forwards to /api/<segment>/...
GATEWAY_ROUTES = (
    "customers",
    "documents",
    "invoices",
    "jobs",
    "messages",
    "subscriptions",
    "tasks",
)

router = Router()
api = router.add


class DemoApp:
    """One demo instance: a context (store, clock, faults, settings) plus its services."""

    def __init__(self, db_path: str | Path) -> None:
        self.ctx = Context(store=Store(db_path))
        ctx = self.ctx
        self.docs = DocumentRenderer(ctx)
        self.ledger = Ledger(ctx)
        self.customers = CustomerService(ctx)
        self.subs = SubscriptionService(ctx, self.ledger, self.customers, self.docs)
        self.scheduler = Scheduler(ctx, self.ledger)
        self.outbox = Outbox(ctx, self.docs)
        self.ref = ReferenceData(ctx)
        self.accounts = AccountsService(ctx)
        self.settlements = Settlements(ctx)
        self.users = UsersService(ctx)
        self.integrations = IntegrationsService(ctx)
        self.exports = ExportsService(ctx, self.docs)
        self.tasks = TasksService(ctx)
        self.ledger.on_booked.append(self.docs.issue_invoice)
        self.ledger.on_booked.append(self.outbox.on_booked)
        if not ctx.store.find("plans", limit=1):
            seed(ctx)

    # -- control ---------------------------------------------------------------

    def reset(self) -> None:
        """Back to a fresh database, real clock, no faults, default settings."""
        with self.ctx.store.lock:
            self.ctx.store.wipe()
            self.ctx.clock.reset()
            self.ctx.faults.reset()
            self.ctx.settings.clear()
            from demo_sut.core import DEFAULT_SETTINGS

            self.ctx.settings.update(
                {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULT_SETTINGS.items()}
            )
            self.ctx.counters.clear()
            self.ctx.cache.clear()
            seed(self.ctx)

    def close(self) -> None:
        for thread in list(self.ctx.background):
            thread.join(timeout=5)
        self.ctx.store.close()

    # -- WSGI ------------------------------------------------------------------

    def __call__(self, environ: dict[str, Any], start_response: Any) -> list[bytes]:
        request = Request.from_environ(environ)
        response = self.handle(request)
        response.header("X-Demo-Faults", self.ctx.faults.header())
        response.header("X-Demo-Clock", self.ctx.clock.header())
        response.header("Cache-Control", "no-store")
        start_response(response.status_line, response.headers)
        return [response.body]

    def handle(self, request: Request) -> Response:
        with self.ctx.store.lock:
            self.ctx.run_tag = request.headers.get("x-test-run-id")
            self.ctx.actor = "anonymous"
            try:
                return self._dispatch(request)
            except DemoError as exc:
                source = "ui" if _is_ui(request.path) else "service"
                return _error_response(exc).header("X-Demo-Source", source)
            except Exception as exc:  # noqa: BLE001 - turn any crash into a 500 body
                traceback.print_exc(file=sys.stderr)
                body = {"error": "internal", "message": f"{type(exc).__name__}: {exc}"}
                return json_response(body, 500).header("X-Demo-Source", "service")
            finally:
                self.ctx.run_tag = None

    def _dispatch(self, request: Request) -> Response:
        path = request.path
        if path.startswith("/gw/") or path == "/gw":
            segment = path.split("/")[2] if path.count("/") >= 2 else ""
            if segment not in GATEWAY_ROUTES:
                body = {
                    "error": "no_route",
                    "source": "gateway",
                    "path": path,
                    "message": "No gateway route matches this path.",
                }
                return json_response(body, 404).header("X-Demo-Source", "gateway")
            self._guard_outage(path)
            request.path = "/api" + path[3:]
            try:
                response = self._dispatch_api(request)
            except DemoError as exc:
                response = _error_response(exc).header("X-Demo-Source", "service")
            return response.header("X-Demo-Gateway", "forwarded")
        if path.startswith("/api/") or path == "/oauth/token":
            return self._dispatch_api(request)
        route, params, known = router.match(request.method, path)
        if route is None:
            return ui.not_found(self, request).header("X-Demo-Source", "ui")
        return route.handler(self, request, **params).header("X-Demo-Source", "ui")

    def _guard_outage(self, path: str) -> None:
        if self.ctx.fault("api_unavailable") and not path.startswith("/api/_admin"):
            raise Unavailable("The service is unavailable.")

    def _dispatch_api(self, request: Request) -> Response:
        path = request.path
        self._guard_outage(path)
        if (
            self.ctx.settings["auth_required"]
            and path.startswith("/api/")
            and not path.startswith(("/api/_admin", "/api/health"))
        ):
            self._authenticate(request)
        route, params, known = router.match(request.method, path)
        if route is None:
            if known:
                body = {"error": "method_not_allowed", "message": f"{request.method} {path}"}
                return json_response(body, 405).header("X-Demo-Source", "service")
            body = {"error": "unknown_path", "message": f"No API route for {path}", "path": path}
            return json_response(body, 404).header("X-Demo-Source", "service")
        response = route.handler(self, request, **params)
        if request.method != "GET" and not path.startswith("/api/_admin"):
            record_audit(
                self.ctx, request.method, path, response.status, (route.tag, params.get("id"))
            )
        return response.header("X-Demo-Source", "service")

    def _authenticate(self, request: Request) -> None:
        header = request.headers.get("authorization", "")
        token = header[7:] if header.lower().startswith("bearer ") else None
        subject = self.users.subject_for(token or request.cookies.get("demo_session"))
        if subject is None:
            raise Unauthorized("a valid bearer token is required")
        self.ctx.actor = subject

    def machine(self, key: str) -> dict[str, Any]:
        path = CONFIG_DIR / f"{key}.machine.yaml"
        if not path.is_file():
            raise NotFound("machine", key)
        return yaml.safe_load(path.read_text())


def _error_response(exc: DemoError) -> Response:
    response = json_response(exc.body(), exc.status)
    if isinstance(exc, Throttled):
        response.header("Retry-After", str(exc.retry_after))
    return response


def _is_ui(path: str) -> bool:
    return not (path.startswith("/api/") or path.startswith("/gw/") or path == "/oauth/token")


def _int(value: Any, name: str, default: int | None = None) -> int:
    if value in (None, ""):
        if default is None:
            raise BadRequest(f"{name} is required")
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise BadRequest(f"{name} must be an integer") from exc


# ============================================================================
# API routes. Handlers take (app, request, **path params) and return a Response.
# ============================================================================

OBJ = {"type": "object"}


@api("GET", "/api/health", "Liveness probe", "platform")
def health(app: DemoApp, req: Request) -> Response:
    return json_response({"status": "ok"})


# -- control (test harness) ------------------------------------------------------


@api("GET", "/api/_admin/state", "Clock, active faults and settings", "control")
def admin_state(app: DemoApp, req: Request) -> Response:
    return json_response(
        {
            "clock": app.ctx.clock.describe(),
            "faults": app.ctx.faults.active(),
            "settings": app.ctx.settings,
        }
    )


@api("POST", "/api/_admin/reset", "Fresh database, real clock, no faults", "control")
def admin_reset(app: DemoApp, req: Request) -> Response:
    app.reset()
    return admin_state(app, req)


@api("GET", "/api/_admin/clock", "Read the demo clock", "control")
def clock_get(app: DemoApp, req: Request) -> Response:
    return json_response(app.ctx.clock.describe())


@api(
    "POST",
    "/api/_admin/clock",
    "Freeze, shift or reset the demo clock",
    "control",
    body={
        "type": "object",
        "properties": {
            "now": {"type": "string"},
            "shift_days": {"type": "number"},
            "reset": {"type": "boolean"},
        },
    },
)
def clock_set(app: DemoApp, req: Request) -> Response:
    body = req.json()
    clock = app.ctx.clock
    try:
        if body.get("reset"):
            clock.reset()
        if body.get("now"):
            clock.freeze(body["now"])
        if body.get("shift_days") or body.get("shift_seconds"):
            clock.shift(
                days=float(body.get("shift_days") or 0),
                seconds=float(body.get("shift_seconds") or 0),
            )
    except ValueError as exc:
        raise BadRequest(f"clock value not understood: {exc}") from exc
    return json_response(clock.describe())


@api("GET", "/api/_admin/faults", "Fault switch catalogue with the active ones", "control")
def faults_get(app: DemoApp, req: Request) -> Response:
    return json_response({"active": app.ctx.faults.active(), "faults": app.ctx.faults.catalogue()})


@api(
    "POST",
    "/api/_admin/faults",
    "Switch faults on/off ({name: bool}) or replace the set ({active: [names]})",
    "control",
    body=OBJ,
)
def faults_set(app: DemoApp, req: Request) -> Response:
    body = req.json()
    try:
        if "active" in body:
            app.ctx.faults.replace(list(body["active"]))
        else:
            app.ctx.faults.set({k: bool(v) for k, v in body.items()})
    except UnknownFault as exc:
        raise BadRequest(f"unknown fault: {exc.args[0]}", known=sorted(FAULTS)) from exc
    return faults_get(app, req)


@api("GET", "/api/_admin/settings", "Behaviour settings", "control")
def settings_get(app: DemoApp, req: Request) -> Response:
    return json_response(app.ctx.settings)


@api("PATCH", "/api/_admin/settings", "Change behaviour settings", "control", body=OBJ)
def settings_patch(app: DemoApp, req: Request) -> Response:
    return json_response(app.ctx.update_settings(req.json()))


@api(
    "POST",
    "/api/_admin/scheduler-mode",
    "Set booking registration mode (all | one_at_a_time)",
    "control",
    body={"type": "object", "properties": {"mode": {"type": "string"}}, "required": ["mode"]},
)
def scheduler_mode(app: DemoApp, req: Request) -> Response:
    return json_response(app.scheduler.set_mode(req.json().get("mode", "")))


# -- auth ------------------------------------------------------------------------------


@api(
    "POST",
    "/oauth/token",
    "Token endpoint (password and client_credentials grants, form-encoded)",
    "auth",
)
def oauth_token(app: DemoApp, req: Request) -> Response:
    form = req.form() if "json" not in req.headers.get("content-type", "") else req.json()
    try:
        return json_response(app.users.issue_token(form))
    except DemoError as exc:
        code = exc.extra.get("error_code", exc.code)
        return json_response({"error": code, "error_description": exc.message}, exc.status)


@api("GET", "/api/me", "The subject of the presented bearer token", "auth")
def me(app: DemoApp, req: Request) -> Response:
    header = req.headers.get("authorization", "")
    subject = app.users.subject_for(header[7:] if header.lower().startswith("bearer ") else None)
    if subject is None:
        raise Unauthorized("a valid bearer token is required")
    return json_response({"subject": subject})


# -- machines --------------------------------------------------------------------------


@api("GET", "/api/machines/{key}", "Lifecycle definition (states, actions, fields)", "machines")
def machine(app: DemoApp, req: Request, key: str) -> Response:
    return json_response(app.machine(key))


# -- customers -------------------------------------------------------------------------

CUSTOMER_BODY = {
    "type": "object",
    "required": ["type", "display_name", "billing_address"],
    "properties": {
        "type": {"type": "string", "enum": ["INDIVIDUAL", "COMPANY"]},
        "display_name": {"type": "string", "maxLength": 64},
        "email": {"type": "string"},
        "phone": {"type": "string"},
        "billing_address": {
            "type": "object",
            "required": ["line1", "postal_code", "city"],
            "properties": {
                "line1": {"type": "string"},
                "line2": {"type": "string"},
                "postal_code": {"type": "string"},
                "city": {"type": "string"},
                "country": {"type": "string"},
            },
        },
        "payout_account": {"type": "string"},
    },
}


@api("POST", "/api/customers", "Create a customer", "customers", status=201, body=CUSTOMER_BODY)
def customer_create(app: DemoApp, req: Request) -> Response:
    return json_response(app.customers.create(req.json()), 201)


@api("GET", "/api/customers", "List customers", "customers")
def customer_list(app: DemoApp, req: Request) -> Response:
    return json_response(app.customers.list())


@api("GET", "/api/customers/{id}", "Read a customer", "customers")
def customer_get(app: DemoApp, req: Request, id: str) -> Response:
    return json_response(app.customers.read(id))


@api("PATCH", "/api/customers/{id}", "Update a customer", "customers", body=OBJ)
def customer_patch(app: DemoApp, req: Request, id: str) -> Response:
    return json_response(app.customers.patch(id, req.json()))


@api("POST", "/api/customers/{id}/actions/verify", "Screen and verify a customer", "customers")
def customer_verify(app: DemoApp, req: Request, id: str) -> Response:
    return json_response(app.customers.verify(id))


@api(
    "POST",
    "/api/account-check",
    "Account holder name check (stub: MATCH / NO_MATCH)",
    "customers",
    body={
        "type": "object",
        "properties": {"account": {"type": "string"}, "name": {"type": "string"}},
    },
)
def account_check(app: DemoApp, req: Request) -> Response:
    return json_response(app.subs.check_account(req.json()))


# -- reference data ---------------------------------------------------------------------


@api("GET", "/api/reseller-terms", "List reseller terms", "reference")
def terms_list(app: DemoApp, req: Request) -> Response:
    return json_response(app.ctx.store.find("reseller_terms"))


@api(
    "POST",
    "/api/reseller-terms",
    "Create reseller terms (share_percent up to the cap, flat_fee per booked period)",
    "reference",
    status=201,
    body={
        "type": "object",
        "required": ["name", "share_percent"],
        "properties": {
            "name": {"type": "string"},
            "share_percent": {"type": "number"},
            "flat_fee": {"type": "number"},
        },
    },
)
def terms_create(app: DemoApp, req: Request) -> Response:
    return json_response(app.ref.create_terms(req.json()), 201)


@api("GET", "/api/reseller-terms/{id}", "Read reseller terms", "reference")
def terms_get(app: DemoApp, req: Request, id: str) -> Response:
    return json_response(app.ref.terms(id))


@api("GET", "/api/partners", "List partners", "reference")
def partners_list(app: DemoApp, req: Request) -> Response:
    return json_response(app.ctx.store.find("partners"))


@api(
    "POST",
    "/api/partners",
    "Create a partner (number issued by the service)",
    "reference",
    status=201,
    body=OBJ,
)
def partners_create(app: DemoApp, req: Request) -> Response:
    return json_response(app.ref.create_partner(req.json()), 201)


@api("GET", "/api/partners/{id}", "Read a partner", "reference")
def partners_get(app: DemoApp, req: Request, id: str) -> Response:
    return json_response(app.ref.partner(id))


@api("GET", "/api/plans", "List plans", "reference")
def plans_list(app: DemoApp, req: Request) -> Response:
    return json_response(app.ctx.store.find("plans"))


@api("POST", "/api/plans", "Create a plan", "reference", status=201, body=OBJ)
def plans_create(app: DemoApp, req: Request) -> Response:
    return json_response(app.ref.create_plan(req.json()), 201)


@api("GET", "/api/plans/{id}", "Read a plan", "reference")
def plans_get(app: DemoApp, req: Request, id: str) -> Response:
    return json_response(app.ref.plan(id))


# -- subscriptions -------------------------------------------------------------------------

SUBSCRIPTION_BODY = {
    "type": "object",
    "required": ["customer_id", "partner_id", "plan_id"],
    "properties": {
        "customer_id": {"type": "integer"},
        "partner_id": {"type": "integer"},
        "plan_id": {"type": "integer"},
        "frequency": {"type": "string", "enum": ["MONTHLY", "QUARTERLY", "YEARLY"]},
        "currency": {"type": "string", "enum": ["EUR"]},
        "start_date": {"type": "string"},
        "start_time": {"type": "string"},
        "term_months": {"type": "integer"},
        "monthly_net": {"type": "number"},
        "spend_cap": {"type": "number", "minimum": 0},
        "addons": {
            "type": "object",
            "properties": {
                "sso": {"type": "boolean"},
                "audit_trail": {"type": "boolean"},
                "sandbox": {"type": "boolean"},
            },
        },
        "data_region": {"type": "string", "enum": ["EU-CENTRAL", "EU-NORTH", "AP-SOUTH"]},
        "workspace_type": {"type": "string", "enum": ["TEAM_SPACE", "SANDBOX", "TRAINING"]},
        "invoice_notes": {"type": "array"},
        "labels": {"type": "array", "items": {"type": "string"}},
        "seat_blocks": {"type": "array"},
        "billing_contact": {"type": "object"},
        "payment_method": {"type": "object"},
        "po_reference": {"type": "string", "maxLength": 35},
        "invoice_delivery": {"type": "string", "enum": ["EMAIL", "PORTAL"]},
        "commission_percentage": {"type": "number"},
        "activate_immediately": {"type": "boolean"},
    },
}


@api(
    "POST",
    "/api/subscriptions",
    "Create a subscription (status draft)",
    "subscriptions",
    status=201,
    body=SUBSCRIPTION_BODY,
)
def sub_create(app: DemoApp, req: Request) -> Response:
    return json_response(app.subs.create(req.json()), 201)


@api(
    "GET",
    "/api/subscriptions",
    "List subscriptions (current versions)",
    "subscriptions",
    query=("status",),
)
def sub_list(app: DemoApp, req: Request) -> Response:
    rows = app.ctx.store.find("subscriptions")
    if req.query.get("status"):
        rows = [r for r in rows if r["status"] == req.query["status"]]
    return json_response([app.subs.view(r["id"]) for r in rows])


@api(
    "GET",
    "/api/subscriptions/{id}",
    "Read a subscription (?version=n for an older one)",
    "subscriptions",
    query=("version",),
)
def sub_get(app: DemoApp, req: Request, id: str) -> Response:
    return json_response(app.subs.view(id, req.query.get("version")))


@api("PATCH", "/api/subscriptions/{id}", "Edit a draft subscription", "subscriptions", body=OBJ)
def sub_patch(app: DemoApp, req: Request, id: str) -> Response:
    return json_response(app.subs.patch(id, req.json()))


@api("GET", "/api/subscriptions/{id}/versions", "All versions of a subscription", "subscriptions")
def sub_versions(app: DemoApp, req: Request, id: str) -> Response:
    current = app.subs.current_version(id)
    rows = [
        {
            "version": v["version"],
            "version_id": v["id"],
            "effective_from": v["effective_from"],
            "cause": v["cause"],
            "current": v["version"] == current,
        }
        for v in app.subs.versions(id)
    ]
    return json_response(rows)


@api(
    "POST",
    "/api/subscriptions/{id}/actions/submit",
    "Submit a draft for payment (optional price body); issues the order summary",
    "subscriptions",
    body={
        "type": "object",
        "properties": {
            "net_amount": {"type": "number"},
            "tax_rate": {"type": "number"},
            "commission_percentage": {"type": "number"},
        },
    },
)
def sub_submit(app: DemoApp, req: Request, id: str) -> Response:
    return json_response(app.subs.submit(id, req.json()))


@api(
    "POST",
    "/api/subscriptions/{id}/actions/confirm",
    "Confirm (activate): schedules the first term and issues the account-opened letter",
    "subscriptions",
)
def sub_confirm(app: DemoApp, req: Request, id: str) -> Response:
    return json_response(app.subs.confirm(id))


@api(
    "POST",
    "/api/subscriptions/{id}/drafts",
    "Open a change / cancel / renew / note draft (409 when one is already open)",
    "subscriptions",
    status=201,
    body={
        "type": "object",
        "required": ["kind"],
        "properties": {
            "kind": {"type": "string", "enum": ["change", "cancel", "renew", "note"]},
            "effective_date": {"type": "string"},
            "changes": {"type": "object"},
            "reason": {"type": "string"},
            "index_percent": {"type": "number"},
            "title": {"type": "string"},
            "description": {"type": "string"},
        },
    },
)
def sub_open_draft(app: DemoApp, req: Request, id: str) -> Response:
    return json_response(app.subs.open_draft(id, req.json()), 201)


@api("GET", "/api/subscriptions/{id}/transactions", "Ledger transactions", "subscriptions")
def sub_transactions(app: DemoApp, req: Request, id: str) -> Response:
    app.subs.view(id)
    return json_response(app.ledger.transactions(int(id)))


@api("GET", "/api/subscriptions/{id}/invoices", "Invoices of a subscription", "invoices")
def sub_invoices(app: DemoApp, req: Request, id: str) -> Response:
    app.subs.view(id)
    return json_response(app.ctx.store.find("invoices", subscription_id=int(id)))


@api(
    "POST",
    "/api/subscriptions/{id}/invoices/process",
    "Book registered transactions whose period starts on or before ?until=",
    "invoices",
    query=("until",),
)
def sub_process(app: DemoApp, req: Request, id: str) -> Response:
    app.subs.view(id)
    until = req.query.get("until") or app.ctx.clock.today().isoformat()
    return json_response(app.scheduler.process_invoices(id, until))


@api("GET", "/api/subscriptions/{id}/documents", "Documents of a subscription", "documents")
def sub_documents(app: DemoApp, req: Request, id: str) -> Response:
    app.subs.view(id)
    return json_response(app.ctx.store.find("documents", subscription_id=int(id)))


@api("GET", "/api/subscriptions/{id}/notes", "Stored notes of a subscription", "subscriptions")
def sub_notes(app: DemoApp, req: Request, id: str) -> Response:
    return json_response(app.subs.notes(id))


@api("GET", "/api/drafts/{id}", "Read a draft (with steps and preview)", "subscriptions")
def draft_get(app: DemoApp, req: Request, id: str) -> Response:
    return json_response(app.subs.draft_view(id))


@api("PATCH", "/api/drafts/{id}", "Autosave draft fields / changes", "subscriptions", body=OBJ)
def draft_patch(app: DemoApp, req: Request, id: str) -> Response:
    return json_response(app.subs.patch_draft(id, req.json()))


@api("POST", "/api/drafts/{id}/actions/confirm", "Confirm a draft", "subscriptions")
def draft_confirm(app: DemoApp, req: Request, id: str) -> Response:
    return json_response(app.subs.confirm_draft(id))


@api("POST", "/api/drafts/{id}/actions/discard", "Discard a draft", "subscriptions")
def draft_discard(app: DemoApp, req: Request, id: str) -> Response:
    return json_response(app.subs.discard_draft(id))


@api(
    "POST",
    "/api/drafts/{id}/actions/accept-ownership",
    "The new owner accepts an ownership transfer (required before confirming it)",
    "subscriptions",
)
def draft_accept_ownership(app: DemoApp, req: Request, id: str) -> Response:
    return json_response(app.subs.accept_ownership(id))


@api(
    "POST",
    "/api/search",
    "Keyset-paged search (resource, text, limit, after | before cursor)",
    "subscriptions",
    body={
        "type": "object",
        "properties": {
            "resource": {"type": "string"},
            "text": {"type": "string"},
            "limit": {"type": "integer"},
            "after": {"type": "string"},
            "before": {"type": "string"},
        },
    },
)
def search_route(app: DemoApp, req: Request) -> Response:
    return json_response(search(app.ctx, req.json()))


@api(
    "POST",
    "/api/quotes/calculate",
    "Price calculation: net (or plan_id), tax, gross and commission",
    "billing",
    body={
        "type": "object",
        "properties": {
            "net_amount": {"type": "number"},
            "plan_id": {"type": "integer"},
            "tax_rate": {"type": "number"},
            "commission_percentage": {"type": "number"},
        },
    },
)
def quote_calculate(app: DemoApp, req: Request) -> Response:
    body = req.json()
    if "net_amount" not in body and body.get("plan_id") is not None:
        plan = app.ref.plan(body["plan_id"])
        body["net_amount"] = str(monthly_net(plan))
        body.setdefault("tax_rate", plan["tax_rate"])
    return json_response(price_quote(app.ctx, body))


# -- invoices, journal, accounts ----------------------------------------------------------


@api("GET", "/api/invoices/{id}", "Read an invoice", "invoices")
def invoice_get(app: DemoApp, req: Request, id: str) -> Response:
    invoice = app.ctx.store.get("invoices", id)
    if invoice is None:
        raise NotFound("invoice", id)
    return json_response(invoice)


@api("GET", "/api/invoices/{id}/journal", "Journal entries of an invoice", "invoices")
def invoice_journal(app: DemoApp, req: Request, id: str) -> Response:
    invoice_get(app, req, id)
    return json_response(app.ctx.store.find("journal", invoice_id=int(id)))


@api(
    "POST",
    "/api/invoices/{id}/pay",
    "Record a payment (whole remaining by default); an Idempotency-Key header makes a "
    "replay return the first payment",
    "invoices",
    body={"type": "object", "properties": {"amount": {"type": "number"}}},
    headers=("Idempotency-Key",),
)
def invoice_pay(app: DemoApp, req: Request, id: str) -> Response:
    key = req.headers.get("idempotency-key")
    return json_response(app.ledger.pay(id, req.json().get("amount"), idempotency_key=key))


@api(
    "GET",
    "/api/journal",
    "Journal entries (?references=a,b filters on any reference)",
    "invoices",
    query=("references",),
)
def journal(app: DemoApp, req: Request) -> Response:
    refs = [r for r in (req.query.get("references") or "").split(",") if r]
    return json_response(app.ledger.journal(refs or None))


@api("GET", "/api/payments/statement", "Payments received", "invoices")
def payments_statement(app: DemoApp, req: Request) -> Response:
    return json_response(app.ctx.store.find("payments"))


@api("GET", "/api/ledger-codes", "Account role -> account number", "finance")
def ledger_codes(app: DemoApp, req: Request) -> Response:
    return json_response(mapping(app.ctx))


@api("GET", "/api/accounts", "Ledger accounts (managed flag)", "finance")
def accounts_list(app: DemoApp, req: Request) -> Response:
    return json_response(app.accounts.list())


@api(
    "PATCH",
    "/api/accounts/{id}",
    "Rename an account (409 when managed)",
    "finance",
    body={"type": "object", "properties": {"name": {"type": "string"}}},
)
def accounts_patch(app: DemoApp, req: Request, id: str) -> Response:
    return json_response(app.accounts.rename(id, req.json()))


@api(
    "GET",
    "/api/settlements",
    "Settlements (?party=<partner id>&tab=open|closed)",
    "finance",
    query=("party", "tab"),
)
def settlements_list(app: DemoApp, req: Request) -> Response:
    return json_response(app.settlements.list(req.query.get("party"), req.query.get("tab")))


@api(
    "POST",
    "/api/settlements",
    "Compute a partner commission settlement",
    "finance",
    status=201,
    body=OBJ,
)
def settlements_create(app: DemoApp, req: Request) -> Response:
    return json_response(app.settlements.create(req.json()), 201)


@api("POST", "/api/settlements/{id}/actions/close", "Close a settlement", "finance")
def settlements_close(app: DemoApp, req: Request, id: str) -> Response:
    return json_response(app.settlements.close(id))


@api(
    "GET",
    "/api/statements",
    "Statement export of a settlement (?settlement=<id>)",
    "finance",
    query=("settlement",),
)
def statements(app: DemoApp, req: Request) -> Response:
    return json_response(app.settlements.statement(_int(req.query.get("settlement"), "settlement")))


# -- jobs ---------------------------------------------------------------------------------


@api(
    "GET",
    "/api/jobs",
    "Jobs, keyset-paged (?after=<id>&limit=n&kind=)",
    "jobs",
    query=("after", "limit", "kind"),
)
def jobs_list(app: DemoApp, req: Request) -> Response:
    return json_response(
        app.scheduler.jobs(
            after=_int(req.query.get("after"), "after", 0),
            limit=_int(req.query.get("limit"), "limit", 50),
            kind=req.query.get("kind"),
        )
    )


@api("GET", "/api/jobs/{id}", "Read (poll) a job", "jobs")
def jobs_get(app: DemoApp, req: Request, id: str) -> Response:
    return json_response(app.scheduler.job(id))


@api(
    "POST",
    "/api/jobs/run",
    "Run the scheduler: book every job due on or before 'until'",
    "jobs",
    body={
        "type": "object",
        "properties": {"until": {"type": "string"}, "subscription_id": {"type": "integer"}},
    },
)
def jobs_run(app: DemoApp, req: Request) -> Response:
    body = req.json()
    return json_response(app.scheduler.run_due(body.get("until"), body.get("subscription_id")))


@api(
    "POST",
    "/api/jobs/billing/run-next",
    "Book the next registered transaction of a subscription now (synchronous)",
    "jobs",
    body={
        "type": "object",
        "required": ["subscription_id"],
        "properties": {"subscription_id": {"type": "integer"}},
    },
)
def jobs_run_next(app: DemoApp, req: Request) -> Response:
    return json_response(app.scheduler.run_next(req.json().get("subscription_id")))


@api("POST", "/api/jobs/renewals/run", "Renew every due, fully booked subscription", "jobs")
def jobs_renewals(app: DemoApp, req: Request) -> Response:
    return json_response(app.subs.run_renewals())


@api("GET", "/api/events", "Internal events", "jobs", query=("kind",))
def events(app: DemoApp, req: Request) -> Response:
    return json_response(app.scheduler.events(req.query.get("kind")))


# -- documents and files ---------------------------------------------------------------------


@api("GET", "/api/templates", "Document templates", "documents")
def templates(app: DemoApp, req: Request) -> Response:
    return json_response(app.docs.templates())


@api(
    "GET", "/api/templates/{id}/source", "Template source text", "documents", produces="text/plain"
)
def template_source(app: DemoApp, req: Request, id: str) -> Response:
    return bytes_response(app.docs.source(id).encode(), "text/plain; charset=utf-8", f"{id}.tpl")


@api(
    "POST",
    "/api/render",
    "Render a template with a payload to PDF (optional watermark)",
    "documents",
    produces="application/pdf",
    body={
        "type": "object",
        "required": ["template"],
        "properties": {
            "template": {"type": "string"},
            "payload": {"type": "object"},
            "watermark": {"type": "string"},
        },
    },
)
def render(app: DemoApp, req: Request) -> Response:
    body = req.json()
    pdf = app.docs.render(
        str(body.get("template")), body.get("payload") or {}, body.get("watermark")
    )
    return bytes_response(pdf, "application/pdf", f"{body.get('template')}.pdf")


@api("GET", "/api/documents/{id}", "Document metadata", "documents")
def document_get(app: DemoApp, req: Request, id: str) -> Response:
    return json_response(app.docs.get(id))


@api(
    "GET", "/api/documents/{id}.pdf", "Document PDF bytes", "documents", produces="application/pdf"
)
def document_pdf(app: DemoApp, req: Request, id: str) -> Response:
    doc = app.docs.get(id)
    meta, body = app.docs.file(doc["file_id"])
    return bytes_response(body, "application/pdf", meta["name"])


@api("GET", "/api/files", "Stored files (metadata)", "documents")
def files_list(app: DemoApp, req: Request) -> Response:
    return json_response(app.ctx.store.find("files"))


@api(
    "GET", "/api/files/{id}", "Stored file bytes", "documents", produces="application/octet-stream"
)
def file_get(app: DemoApp, req: Request, id: str) -> Response:
    meta, body = app.docs.file(id)
    return bytes_response(body, meta["content_type"], meta["name"])


# -- messages ---------------------------------------------------------------------------------


@api(
    "GET",
    "/api/messages",
    "Outbound messages, newest first (?subscription_id=&since=)",
    "messages",
    query=("subscription_id", "since"),
)
def messages_list(app: DemoApp, req: Request) -> Response:
    return json_response(app.outbox.list(req.query.get("subscription_id"), req.query.get("since")))


@api(
    "POST",
    "/api/messages/generate",
    "Generate files for NEW messages as a background job (202 + job id)",
    "messages",
    status=202,
    body={"type": "object", "properties": {"subscription_id": {"type": "integer"}}},
)
def messages_generate(app: DemoApp, req: Request) -> Response:
    job = app.outbox.generate(req.json().get("subscription_id"))
    return json_response({"job_id": job["id"], "state": job["state"]}, 202).header(
        "Location", f"/api/jobs/{job['id']}"
    )


@api(
    "POST",
    "/api/messages/send",
    "Send TO_BE_SENT messages in the configured file order",
    "messages",
    body={"type": "object", "properties": {"subscription_id": {"type": "integer"}}},
)
def messages_send(app: DemoApp, req: Request) -> Response:
    return json_response(app.outbox.send(req.json().get("subscription_id")))


@api("GET", "/api/messages/{id}", "Message details (files appear once generated)", "messages")
def message_get(app: DemoApp, req: Request, id: str) -> Response:
    return json_response(app.outbox.get(id))


@api(
    "GET",
    "/api/messages/{id}/content",
    "Segment payload of a message (404 while the message is NEW)",
    "messages",
    produces="text/plain",
    query=("format",),
)
def message_content(app: DemoApp, req: Request, id: str) -> Response:
    kind = "XML" if req.query.get("format") == "xml" else "SEGMENTS"
    meta, body = app.outbox.content(id, kind)
    return bytes_response(body, meta["content_type"], meta["name"])


# -- admin ---------------------------------------------------------------------------------------


@api("GET", "/api/tasks", "Tasks", "admin")
def tasks_list(app: DemoApp, req: Request) -> Response:
    return json_response(app.tasks.list())


@api(
    "POST",
    "/api/tasks",
    "Create a task (target_at is an instant, UTC)",
    "admin",
    status=201,
    body={
        "type": "object",
        "required": ["title"],
        "properties": {
            "title": {"type": "string"},
            "subscription_id": {"type": "integer"},
            "target_at": {"type": "string"},
        },
    },
)
def tasks_create(app: DemoApp, req: Request) -> Response:
    return json_response(app.tasks.create(req.json()), 201)


@api("POST", "/api/tasks/{id}/actions/complete", "Complete a task", "admin")
def tasks_complete(app: DemoApp, req: Request, id: str) -> Response:
    return json_response(app.tasks.complete(id))


@api("GET", "/api/users", "Users", "admin")
def users_list(app: DemoApp, req: Request) -> Response:
    return json_response(app.users.list())


@api(
    "POST",
    "/api/users/{id}/roles",
    "Assign / revoke roles",
    "admin",
    body={
        "type": "object",
        "properties": {"assign": {"type": "array"}, "revoke": {"type": "array"}},
    },
)
def users_roles(app: DemoApp, req: Request, id: str) -> Response:
    return json_response(app.users.change_roles(id, req.json()))


@api(
    "GET",
    "/api/users/{id}/role-history",
    "Role events (?since=&until= instants)",
    "admin",
    query=("since", "until"),
)
def users_role_history(app: DemoApp, req: Request, id: str) -> Response:
    return json_response(app.users.role_history(id, req.query.get("since"), req.query.get("until")))


@api("GET", "/api/integrations/services", "Integration services and their parameters", "admin")
def integration_services(app: DemoApp, req: Request) -> Response:
    return json_response(app.integrations.services())


@api("GET", "/api/integrations", "Registered integrations", "admin")
def integrations_list(app: DemoApp, req: Request) -> Response:
    return json_response(app.integrations.list())


@api(
    "POST",
    "/api/integrations/{svc}",
    "Register an integration (baseUrl optional; https public hosts only)",
    "admin",
    status=201,
    body=OBJ,
)
def integrations_register(app: DemoApp, req: Request, svc: str) -> Response:
    return json_response(app.integrations.register(svc, req.json()), 201)


@api("GET", "/api/exports", "Exports", "admin")
def exports_list(app: DemoApp, req: Request) -> Response:
    return json_response(app.exports.list())


@api(
    "POST",
    "/api/exports",
    "Create an export archive (kind audit | subscriptions)",
    "admin",
    status=201,
    body={"type": "object", "properties": {"kind": {"type": "string"}}},
)
def exports_create(app: DemoApp, req: Request) -> Response:
    return json_response(app.exports.create(req.json()), 201)


@api("GET", "/api/exports/{id}", "Read an export", "admin")
def exports_get(app: DemoApp, req: Request, id: str) -> Response:
    return json_response(app.exports.get(id))


@api(
    "GET",
    "/api/exports/{id}/download",
    "Download an export archive (zip)",
    "admin",
    produces="application/zip",
)
def exports_download(app: DemoApp, req: Request, id: str) -> Response:
    export = app.exports.get(id)
    meta, body = app.docs.file(export["file_id"])
    return bytes_response(body, "application/zip", meta["name"])


@api(
    "GET",
    "/api/audit",
    "Audit trail of writes (?since=&entity=)",
    "admin",
    query=("since", "entity"),
)
def audit(app: DemoApp, req: Request) -> Response:
    rows = app.ctx.store.find("audit")
    since, entity = req.query.get("since"), req.query.get("entity")
    since = normalize_instant(since) if since else None
    rows = [
        r
        for r in rows
        if (not since or r["at"] >= since) and (not entity or r["entity_type"] == entity)
    ]
    return json_response(rows)


ui.register(router)
message_attachments.register(router)
