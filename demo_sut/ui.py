"""Server-rendered screens.

Locator contract: every element a test needs carries a ``data-testid`` (and, where it
applies, an ARIA role/label); lifecycle buttons carry ``data-action`` named exactly like the
actions in ``config/subscription.machine.yaml``; state pills carry ``data-state``. No test
should need a CSS class or an XPath.

Every page shows the fault banner (``[data-testid=demo-faults-banner]``, attribute
``data-faults``) and has an error banner (``[data-testid=error-banner]``, ``role=alert``)
that page scripts fill when an API call fails.
"""

from __future__ import annotations

import json
from datetime import datetime
from html import escape
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from demo_sut.billing import ADDONS
from demo_sut.core import NotFound
from demo_sut.customers import CUSTOMER_TYPES, HOME_COUNTRY
from demo_sut.http import Request, Response, Router, html_response, redirect
from demo_sut.subscriptions import (
    CLOSURE_REASONS,
    DATA_REGIONS,
    INVOICE_DELIVERY,
    LABELS,
    WORKSPACE_TYPES,
)

if TYPE_CHECKING:
    from demo_sut.app import DemoApp

MENU = [
    ("subscriptions", "Subscriptions", "/ui/subscriptions"),
    ("new-subscription", "New subscription", "/ui/subscriptions/new"),
    ("customers", "Customers", "/ui/customers"),
    ("tasks", "Tasks", "/ui/tasks"),
    ("admin", "Admin", "/ui/admin"),
]

#: Admin cards. "Archived lists" was retired from the cards; its page is still reachable.
ADMIN_CARDS = [
    ("reference", "Reference data", [("plans", "Plans"), ("partners", "Partners")]),
    ("finance", "Finance", [("accounts", "Ledger accounts"), ("settlements", "Settlements")]),
    (
        "operations",
        "Operations",
        [
            ("jobs", "Jobs"),
            ("integrations", "Integrations"),
            ("users", "Users"),
            ("exports", "Exports"),
        ],
    ),
]


def e(value: Any) -> str:
    return escape("" if value is None else str(value), quote=True)


STYLE = """
body{font-family:system-ui,sans-serif;margin:0;color:#1d2433;background:#f7f8fa}
header{background:#24324a;color:#fff;padding:8px 16px;display:flex;gap:16px;align-items:center}
header a{color:#fff}
.layout{display:flex;min-height:100vh}
nav.side{width:190px;background:#e9edf3;padding:12px}
nav.side a{display:block;padding:6px 4px;color:#1d2433}
main{flex:1;padding:16px 20px;max-width:1100px}
.faults{padding:4px 10px;border-radius:4px;background:#dfe7d8;color:#24324a;font-size:13px}
.faults[data-active=true]{background:#ffd27a}
.error{background:#fde2e1;border:1px solid #d9534f;padding:8px;margin-bottom:12px}
table{border-collapse:collapse;width:100%;background:#fff}
th,td{border:1px solid #d5dbe5;padding:4px 8px;text-align:left;font-size:14px}
.pill{display:inline-block;padding:2px 8px;border-radius:10px;background:#dbe6ff}
.card{background:#fff;border:1px solid #d5dbe5;padding:10px;margin:8px 0}
.field{margin:6px 0}.field label{display:inline-block;min-width:160px}
.inline-error{color:#b52b27;font-size:13px}
.hint{color:#5d6b82;font-size:12px}
[role=menu]{background:#fff;border:1px solid #aab;padding:4px;position:absolute}
"""

COMMON_JS = r"""
const demo = {
  banner() { return document.querySelector('[data-testid="error-banner"]'); },
  error(message) { const b = demo.banner(); b.textContent = message; b.hidden = false; },
  clearError() { const b = demo.banner(); b.textContent = ''; b.hidden = true; },
  async call(url, options) {
    const opts = Object.assign({method: 'GET'}, options || {});
    opts.headers = Object.assign({'Content-Type': 'application/json',
                                  'Accept': 'application/json'}, opts.headers || {});
    if (opts.json !== undefined) { opts.body = JSON.stringify(opts.json); delete opts.json; }
    let response;
    try { response = await fetch(url, opts); }
    catch (err) { demo.error('Request failed: ' + err); throw err; }
    const type = response.headers.get('content-type') || '';
    const body = type.includes('json') ? await response.json() : null;
    if (!response.ok) {
      const message = (body && body.message) || response.statusText;
      demo.error('Request failed (' + response.status + '): ' + message);
      const failure = new Error(message); failure.status = response.status;
      failure.body = body; throw failure;
    }
    return body;
  },
  esc(value) {
    return String(value === null || value === undefined ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  },
  data() {
    const node = document.getElementById('page-data');
    return node ? JSON.parse(node.textContent) : {};
  },
  fieldErrors(errors, prefix) {
    document.querySelectorAll('[data-field-error]').forEach(n => { n.textContent = ''; });
    Object.entries(errors || {}).forEach(([path, message]) => {
      const key = (prefix || '') + path.replace(/\[(\d+)\]/g, (m, i) => '-' + (Number(i) + 1))
        .replace(/\./g, '-');
      const node = document.querySelector('[data-testid="error-' + key + '"]');
      if (node) node.textContent = message;
    });
  },
};
"""


def layout(
    app: DemoApp,
    title: str,
    body: str,
    *,
    page_data: Any = None,
    script: str = "",
    breadcrumbs: list[tuple[str, str | None]] | None = None,
    status: int = 200,
    error: str | None = None,
) -> Response:
    faults = app.ctx.faults.active()
    if app.ctx.fault("api_unavailable"):
        error = "The service is unavailable. Try again later."
        body = '<p data-testid="page-unavailable">This page cannot be shown right now.</p>'
        script, status = "", 503
    banner = (
        f'<div class="faults" data-testid="demo-faults-banner" role="status" '
        f'data-faults="{e(",".join(faults))}" data-active="{str(bool(faults)).lower()}">'
        f"Active faults: {e(', '.join(faults) or 'none')}</div>"
    )
    menu = "".join(
        f'<a href="{href}" data-testid="menu-{key}">{e(label)}</a>' for key, label, href in MENU
    )
    crumbs = ""
    if breadcrumbs:
        parts = [
            f'<a href="{href}">{e(label)}</a>'
            if href
            else f'<span aria-current="page">{e(label)}</span>'
            for label, href in breadcrumbs
        ]
        crumbs = (
            '<nav aria-label="Breadcrumb" data-testid="breadcrumb">'
            + " &rsaquo; ".join(parts)
            + "</nav>"
        )
    data = ""
    if page_data is not None:
        payload = json.dumps(page_data).replace("</", "<\\/")
        data = f'<script type="application/json" id="page-data">{payload}</script>'

    hidden = "" if error else " hidden"
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{e(title)} - Demo subscriptions</title>
<style>{STYLE}</style></head>
<body data-clock="{e(app.ctx.clock.describe()["now"])}">
<header><strong data-testid="app-title">Demo subscriptions</strong>{banner}
<a href="/login" data-testid="login-link">Log in</a></header>
<div class="layout">
<nav class="side" aria-label="Main menu" data-testid="side-menu">{menu}</nav>
<main>
<div class="error" data-testid="error-banner" role="alert"{hidden}>{e(error)}</div>
{crumbs}
<h1 data-testid="page-title">{e(title)}</h1>
{body}
</main></div>
{data}
<script>{COMMON_JS}</script>
<script>{script}</script>
</body></html>"""
    return html_response(html, status)


def not_found(app: DemoApp, request: Request) -> Response:
    return layout(
        app,
        "Page not found",
        f'<p data-testid="not-found">No page at {e(request.path)}.</p>',
        status=404,
    )


def table(rows: list[dict[str, Any]], columns: list[tuple[str, str]], testid: str) -> str:
    head = "".join(f"<th>{e(label)}</th>" for _, label in columns)
    body = "".join(
        f'<tr data-testid="{testid}-row" data-id="{e(r.get("id"))}">'
        + "".join(f'<td data-col="{key}">{e(r.get(key))}</td>' for key, _ in columns)
        + "</tr>"
        for r in rows
    )
    return (
        f'<table data-testid="{testid}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'
    )


def options_html(values: list[tuple[str, str]], selected: Any = None, blank: bool = False) -> str:
    out = '<option value="">--</option>' if blank else ""
    for value, label in values:
        mark = " selected" if str(value) == str(selected) else ""
        out += f'<option value="{e(value)}"{mark}>{e(label)}</option>'
    return out


# ============================================================================
# login / index
# ============================================================================


def login_page(app: DemoApp, req: Request) -> Response:
    error = None
    if req.method == "POST":
        form = req.form()
        token = app.users.login(form.get("username", ""), form.get("password", ""))
        if token:
            response = redirect(form.get("next") or "/ui")
            response.header("Set-Cookie", f"demo_session={token}; Path=/; HttpOnly")
            return response
        error = "Invalid username or password."
    alert = (
        f'<p class="inline-error" role="alert" data-testid="login-error">{e(error)}</p>'
        if error
        else ""
    )
    body = f"""
<form method="post" action="/login" data-testid="login-form" aria-label="Log in">
{alert}
<div class="field"><label for="username">Username</label>
<input id="username" name="username" data-testid="login-username" autocomplete="username"></div>
<div class="field"><label for="password">Password</label>
<input id="password" name="password" type="password" data-testid="login-password"></div>
<button type="submit" data-testid="login-submit">Log in</button>
</form>"""
    return layout(app, "Log in", body, status=401 if error else 200)


def logout(app: DemoApp, req: Request) -> Response:
    response = redirect("/login")
    response.header("Set-Cookie", "demo_session=; Path=/; Max-Age=0")
    return response


def root(app: DemoApp, req: Request) -> Response:
    return redirect("/ui")


def favicon(app: DemoApp, req: Request) -> Response:
    return Response(204)


def index(app: DemoApp, req: Request) -> Response:
    store = app.ctx.store
    counts = [
        ("subscriptions", "Subscriptions", len(store.find("subscriptions"))),
        ("customers", "Customers", len(store.find("customers"))),
        ("tasks", "Open tasks", len(store.find("tasks", state="OPEN"))),
    ]
    cards = "".join(
        f'<div class="card" data-testid="dashboard-{key}"><strong>{e(label)}</strong>: '
        f'<span data-testid="dashboard-{key}-count">{n}</span></div>'
        for key, label, n in counts
    )
    return layout(app, "Dashboard", cards)


# ============================================================================
# subscriptions: list, detail, wizard, drafts
# ============================================================================

LIST_JS = r"""
const state = {text: '', after: null, before: null, next: null, prev: null};
const limit = demo.data().limit;
async function load(direction) {
  const body = {resource: 'subscriptions', text: state.text, limit: limit};
  if (direction === 'next') body.after = state.next;
  if (direction === 'prev') body.before = state.prev;
  const result = await demo.call('/api/search', {method: 'POST', json: body});
  const rows = result.items.map(s => (
    '<tr data-testid="subscription-row" data-id="' + s.id + '">' +
    '<td><a data-testid="subscription-link" href="/ui/subscriptions/' + s.id + '">' +
    demo.esc(s.number) + '</a></td>' +
    '<td data-testid="subscription-title">' + demo.esc(s.title) + '</td>' +
    '<td><span class="pill" data-testid="status-pill" data-state="' + demo.esc(s.status) + '">' +
    demo.esc(s.status) + '</span></td></tr>')).join('');
  document.querySelector('[data-testid="subscriptions-table"] tbody').innerHTML = rows;
  state.next = result.next_cursor; state.prev = result.prev_cursor;
  document.querySelector('[data-testid="page-next"]').disabled = !state.next;
  document.querySelector('[data-testid="page-prev"]').disabled = !state.prev;
  document.querySelector('[data-testid="result-count"]').textContent = result.total;
}
function applyFilter() {
  state.text = document.querySelector('[data-testid="filter-input"]').value.trim();
  const chip = document.querySelector('[data-testid="filter-chip"]');
  chip.hidden = !state.text;
  document.querySelector('[data-testid="filter-chip-text"]').textContent = state.text;
  load();
}
document.querySelector('[data-testid="filter-input"]').addEventListener('keydown', ev => {
  if (ev.key === 'Enter') { ev.preventDefault(); applyFilter(); }
});
document.querySelector('[data-testid="filter-apply"]').addEventListener('click', applyFilter);
document.querySelector('[data-testid="filter-chip-remove"]').addEventListener('click', () => {
  document.querySelector('[data-testid="filter-input"]').value = ''; applyFilter();
});
document.querySelector('[data-testid="page-next"]').addEventListener('click', () => load('next'));
document.querySelector('[data-testid="page-prev"]').addEventListener('click', () => load('prev'));
load();
"""


def subscription_list(app: DemoApp, req: Request) -> Response:
    limit = int(req.query.get("limit") or 10)
    if app.ctx.faults.is_on("screen_filter_unlabelled"):
        filter_field = (
            '<div class="field"><span>Filter</span>\n<input id="filter" data-testid="filter-input">'
        )
    else:
        filter_field = (
            '<div class="field"><label for="filter">Filter</label>\n'
            '<input id="filter" data-testid="filter-input" aria-label="Filter subscriptions">'
        )
    body = (
        filter_field
        + """
<button type="button" data-testid="filter-apply">Apply</button>
<span data-testid="filter-chip" hidden>Filter: <span data-testid="filter-chip-text"></span>
<button type="button" data-testid="filter-chip-remove" aria-label="Remove filter">x</button>
</span></div>
<p>Results: <span data-testid="result-count"></span></p>
<table data-testid="subscriptions-table"><thead><tr><th>Number</th><th>Title</th><th>Status</th>
</tr></thead><tbody></tbody></table>
<div role="navigation" aria-label="Pages">
<button type="button" data-testid="page-prev" disabled>Previous</button>
<button type="button" data-testid="page-next" disabled>Next</button></div>"""
    )
    return layout(app, "Subscriptions", body, page_data={"limit": limit}, script=LIST_JS)


def machine_actions(app: DemoApp, status: str) -> list[tuple[str, str]]:
    spec = app.machine("subscription")
    titles = {k: v["title"] for k, v in spec["actions"].items()}
    state = spec["states"].get(status) or {}
    out = []
    for transition in state.get("transitions") or []:
        name = transition["action"]
        if name.startswith("confirm_") or name == "discard_draft":
            continue  # these live on the draft screen
        out.append((name, titles[name]))
    return out


DETAIL_JS = r"""
const page = demo.data();
async function loadVersion() {
  const v = await demo.call('/api/subscriptions/' + page.id + '?version=' + page.version);
  const pill = document.querySelector('[data-testid="status-pill"]');
  pill.textContent = v.status; pill.dataset.state = v.status;
  const set = (id, value) => {
    document.querySelector('[data-testid="' + id + '"]').textContent = value ?? '';
  };
  set('version-number', v.version); set('version-effective', v.effective_from);
  set('version-status', v.version_status);
  set('holder-name', v.holder && v.holder.name);
  set('holder-type', v.holder && v.holder.type);
  set('plan-kind', v.plan_kind); set('monthly-gross', v.price.monthly_gross.toFixed(2));
  set('data-region', v.data_region); set('workspace-type', v.workspace_type);
  set('term-end', v.term_end); set('seat-total', v.seat_total);
  set('billing-contact', v.billing_contact && v.billing_contact.name);
  set('po-reference', v.po_reference); set('invoice-delivery', v.invoice_delivery);
  set('addons', Object.entries(v.addons).filter(([k, on]) => on).map(([k]) => k).join(', '));
  document.querySelector('[data-testid="version-panel"]').dataset.current = v.current;
}
document.querySelector('[data-testid="version-select"]').addEventListener('change', ev => {
  window.location.href = '/ui/subscriptions/' + page.id + '?version=' + ev.target.value;
});
document.querySelectorAll('[data-action]').forEach(button => {
  button.addEventListener('click', async () => {
    demo.clearError();
    const action = button.dataset.action;
    try {
      if (['change', 'cancel', 'renew', 'note'].includes(action)) {
        const draft = await demo.call('/api/subscriptions/' + page.id + '/drafts',
                                      {method: 'POST', json: {kind: action}});
        window.location.href = '/ui/drafts/' + draft.id;
      } else if (action === 'submit') {
        window.location.href = '/ui/subscriptions/' + page.id + '/step/quote';
      } else {
        await demo.call('/api/subscriptions/' + page.id + '/actions/' +
                        (action === 'activate' ? 'confirm' : action), {method: 'POST'});
        window.location.reload();
      }
    } catch (err) { /* banner already shows the failure */ }
  });
});
const gen = document.querySelector('[data-testid="generate-messages"]');
gen.addEventListener('click', async () => {
  const status = document.querySelector('[data-testid="job-status"]');
  const started = await demo.call('/api/messages/generate',
                                  {method: 'POST', json: {subscription_id: page.id}});
  status.textContent = 'RUNNING'; status.dataset.jobId = started.job_id;
  for (let i = 0; i < 100; i++) {
    await new Promise(r => setTimeout(r, 100));
    const job = await demo.call('/api/jobs/' + started.job_id);
    status.textContent = job.state;
    if (job.state === 'DONE') { window.location.reload(); return; }
  }
});
loadVersion();
"""


def subscription_detail(app: DemoApp, req: Request, id: str) -> Response:
    try:
        view = app.subs.view(id, req.query.get("version"))
    except NotFound:
        return not_found(app, req)
    sub_id = view["id"]
    versions = app.subs.versions(sub_id)
    version_opts = options_html(
        [(v["version"], f"Version {v['version']} (from {v['effective_from']})") for v in versions],
        view["version"],
    )
    actions = ""
    if view["current"]:
        actions = "".join(
            f'<button type="button" data-action="{e(name)}" data-testid="action-{e(name)}">'
            f"{e(title)}</button>"
            for name, title in machine_actions(app, view["status"])
            if name != "activate"
            or (app.ctx.store.get("subscriptions", sub_id) or {}).get("activate_immediately")
        )
    draft_link = ""
    if view["open_draft_id"]:
        draft_link = (
            f'<a data-testid="open-draft-link" href="/ui/drafts/{view["open_draft_id"]}">'
            "Continue the open draft</a>"
        )
    txs = app.ledger.transactions(sub_id)
    ledger_rows = "".join(
        f'<tr data-testid="ledger-row" data-transaction-id="{t["id"]}" data-state="{t["state"]}">'
        f'<td data-col="kind">{t["kind"]}</td><td data-col="period_start">{t["period_start"]}</td>'
        f'<td data-col="period_end">{t["period_end"]}</td><td data-col="net">{t["net"]:.2f}</td>'
        f'<td data-col="tax">{t["tax"]:.2f}</td><td data-col="fee">{t["fee_net"]:.2f}</td>'
        f'<td data-col="total">{t["total"]:.2f}</td><td data-col="state">{t["state"]}</td></tr>'
        for t in txs
    )
    docs = app.ctx.store.find("documents", subscription_id=sub_id)
    doc_rows = "".join(
        f'<tr data-testid="document-row" data-id="{d["id"]}"><td>{e(d["template"])}</td>'
        f'<td>v{d["version"]}</td><td><a data-testid="download-document" '
        f'href="/api/documents/{d["id"]}.pdf" download="{e(d["file_name"])}">Download document'
        f"</a></td></tr>"
        for d in docs
    )
    messages = app.outbox.list(sub_id)
    msg_rows = "".join(
        f'<tr data-testid="message-row" data-id="{e(m["id"])}" data-status="{e(m["status"])}">'
        f"<td>{e(m['type'])}</td><td>{e(m['status'])}</td><td>"
        + (
            f'<a data-testid="download-message" href="/api/messages/{e(m["id"])}/content" '
            f'download="{e(m["files"][0]["name"])}">Download message</a>'
            if m["files"]
            else "-"
        )
        + "</td></tr>"
        for m in messages
    )
    notes = "".join(
        f'<li data-testid="note-item"><strong data-testid="note-title">{e(n["title"])}</strong> '
        f'<span data-testid="note-description">{e(n["description"])}</span></li>'
        for n in app.subs.notes(sub_id)
    )
    body = f"""
<section data-testid="version-panel" aria-label="Version">
<p><span class="pill" data-testid="status-pill" data-state="{e(view["status"])}">
{e(view["status"])}</span>
<strong data-testid="subscription-number">{e(view["number"])}</strong></p>
<div class="field"><label for="version-select">Version</label>
<select id="version-select" data-testid="version-select">{version_opts}</select></div>
<dl>
<dt>Version</dt><dd data-testid="version-number"></dd>
<dt>Version status</dt><dd data-testid="version-status"></dd>
<dt>Effective from</dt><dd data-testid="version-effective"></dd>
<dt>Holder</dt><dd data-testid="holder-name"></dd>
<dt>Holder type</dt><dd data-testid="holder-type"></dd>
<dt>Plan</dt><dd data-testid="plan-kind"></dd>
<dt>Monthly gross</dt><dd data-testid="monthly-gross"></dd>
<dt>Data region</dt><dd data-testid="data-region"></dd>
<dt>Workspace</dt><dd data-testid="workspace-type"></dd>
<dt>Seats</dt><dd data-testid="seat-total"></dd>
<dt>Billing contact</dt><dd data-testid="billing-contact"></dd>
<dt>PO reference</dt><dd data-testid="po-reference"></dd>
<dt>Invoice delivery</dt><dd data-testid="invoice-delivery"></dd>
<dt>Add-ons</dt><dd data-testid="addons"></dd>
<dt>Term end</dt><dd data-testid="term-end"></dd>
</dl></section>
<div role="toolbar" aria-label="Actions" data-testid="actions">{actions} {draft_link}</div>
<section id="ledger" aria-label="Ledger"><h2>Ledger</h2>
<table data-testid="ledger-grid"><thead><tr><th>Kind</th><th>Period start</th><th>Period end</th>
<th>Net</th><th>Tax</th><th>Fee</th><th>Total</th><th>State</th></tr></thead>
<tbody>{ledger_rows}</tbody></table></section>
<section id="downloads" aria-label="Documents"><h2>Documents</h2>
<table data-testid="documents-table"><tbody>{doc_rows}</tbody></table></section>
<section id="messages" aria-label="Messages"><h2>Messages</h2>
<button type="button" data-testid="generate-messages">Generate messages</button>
<span data-testid="job-status" role="status"></span>
<table data-testid="messages-table"><tbody>{msg_rows}</tbody></table></section>
<section aria-label="Notes"><h2>Notes</h2><ul data-testid="notes-list">{notes}</ul></section>
"""
    data = {"id": sub_id, "version": view["version"]}
    crumbs = [("Subscriptions", "/ui/subscriptions"), (view["number"], None)]
    return layout(
        app,
        f"Subscription {view['number']}",
        body,
        page_data=data,
        script=DETAIL_JS,
        breadcrumbs=crumbs,
    )


PICKER_HTML = """
<dialog data-testid="picker-dialog" aria-label="Select a record">
<form method="dialog">
<div class="field"><label for="picker-search">Search</label>
<input id="picker-search" data-testid="picker-search">
<button type="button" data-testid="picker-search-submit">Search</button></div>
<table data-testid="picker-results"><thead><tr><th>Number</th><th>Name</th><th></th></tr>
</thead><tbody></tbody></table>
<button value="cancel" data-testid="picker-cancel">Cancel</button>
</form></dialog>"""

PICKER_JS = r"""
const picker = {
  resource: null, onPick: null,
  dialog() { return document.querySelector('[data-testid="picker-dialog"]'); },
  async search() {
    const text = document.querySelector('[data-testid="picker-search"]').value;
    const result = await demo.call('/api/search', {method: 'POST',
      json: {resource: picker.resource, text: text, limit: 25}});
    const body = picker.dialog().querySelector('tbody');
    body.innerHTML = result.items.map(item => (
      '<tr data-testid="picker-row" data-id="' + item.id + '"><td data-testid="picker-number">' +
      demo.esc(item.number) + '</td><td data-testid="picker-name">' + demo.esc(item.name) +
      '</td><td><button type="button" data-testid="picker-choose" data-id="' + item.id +
      '">Choose</button></td></tr>')).join('');
    body.querySelectorAll('[data-testid="picker-choose"]').forEach(btn => {
      btn.addEventListener('click', () => {
        const item = result.items.find(i => String(i.id) === btn.dataset.id);
        picker.dialog().close(); picker.onPick(item);
      });
    });
  },
  open(resource, onPick) {
    picker.resource = resource; picker.onPick = onPick;
    document.querySelector('[data-testid="picker-search"]').value = '';
    picker.dialog().showModal(); picker.search();
  },
};
document.querySelector('[data-testid="picker-search-submit"]').addEventListener('click',
  () => picker.search());
// Enter in the search box searches (instead of submitting the dialog form, which closes it),
// and a click anywhere on a result row picks it, like the row's own Choose button.
document.querySelector('[data-testid="picker-search"]').addEventListener('keydown', ev => {
  if (ev.key === 'Enter') { ev.preventDefault(); picker.search(); }
});
picker.dialog().querySelector('tbody').addEventListener('click', ev => {
  const row = ev.target.closest('[data-testid="picker-row"]');
  if (!row || ev.target.closest('button')) return;
  row.querySelector('[data-testid="picker-choose"]').click();
});
"""

WIZARD_JS = r"""
const chosen = {partner: null, customer: null};
async function showCustomer(item) {
  chosen.customer = item;
  document.querySelector('[data-testid="customer-summary"]').textContent =
    item.number + ' ' + item.name;
  document.querySelector('[data-testid="customer-change"]').hidden = false;
  document.querySelector('[data-testid="pick-customer"]').hidden = true;
  const customer = await demo.call('/api/customers/' + item.id);
  const verification = document.querySelector('[data-testid="step-verification"]');
  verification.hidden = customer.status === 'verified';
  verification.dataset.customerId = customer.id;
  const a = customer.billing_address || {};
  const block = document.querySelector('[data-testid="address-block"]');
  block.dataset.country = a.country || '';
  block.textContent = [a.line1, a.line2, a.postal_code, a.city, a.country]
    .filter(Boolean).join(', ') || '-';
}
function pickFor(kind) {
  picker.open(kind === 'partner' ? 'partners' : 'customers', async item => {
    if (kind === 'customer') { await showCustomer(item); return; }
    chosen.partner = item;
    document.querySelector('[data-testid="partner-summary"]').textContent =
      item.number + ' ' + item.name;
    document.querySelector('[data-testid="partner-change"]').hidden = false;
    document.querySelector('[data-testid="pick-partner"]').hidden = true;
  });
}
document.querySelector('[data-testid="customer-number-lookup"]').addEventListener('click',
  async () => {
    const status = document.querySelector('[data-testid="customer-number-status"]');
    const number = document.querySelector('[data-testid="customer-number"]').value.trim();
    const result = await demo.call('/api/search', {method: 'POST',
      json: {resource: 'customers', text: number, limit: 25}});
    const match = result.items.filter(i => i.number === number);
    if (match.length !== 1) { status.textContent = 'No customer with number ' + number; return; }
    status.textContent = '';
    await showCustomer(match[0]);
  });
['partner', 'customer'].forEach(kind => {
  document.querySelector('[data-testid="pick-' + kind + '"]').addEventListener('click',
    () => pickFor(kind));
  document.querySelector('[data-testid="' + kind + '-change"]').addEventListener('click',
    () => pickFor(kind));
});
document.querySelector('[data-testid="screening-run"]').addEventListener('click', async () => {
  const step = document.querySelector('[data-testid="step-verification"]');
  const customer = await demo.call('/api/customers/' + step.dataset.customerId +
                                   '/actions/verify', {method: 'POST'});
  document.querySelector('[data-testid="screening-status"]').textContent = customer.status;
});
document.querySelector('[data-testid="parties-submit"]').addEventListener('click', async () => {
  demo.clearError();
  const error = document.querySelector('[data-testid="step-error"]');
  error.textContent = '';
  const body = {
    partner_id: chosen.partner && chosen.partner.id,
    customer_id: chosen.customer && chosen.customer.id,
    plan_id: Number(document.querySelector('[data-testid="plan-select"]').value),
    frequency: document.querySelector('[data-testid="frequency-select"]').value,
    start_date: document.querySelector('[data-testid="start-date"]').value,
    data_region: document.querySelector('[data-testid="data-region-select"]').value,
    workspace_type: document.querySelector('[data-testid="workspace-select"]').value || null,
    invoice_delivery: document.querySelector('[data-testid="delivery-select"]').value,
  };
  try {
    const sub = await demo.call('/api/subscriptions', {method: 'POST', json: body});
    window.location.href = '/ui/subscriptions/' + sub.id + '/step/quote';
  } catch (err) { error.textContent = err.message; }
});
"""


def wizard(app: DemoApp, req: Request) -> Response:
    plans = app.ctx.store.find("plans")
    plan_opts = options_html([(p["id"], f"{p['name']} ({p['kind']})") for p in plans])
    freq_opts = options_html([(f, f.title()) for f in ("MONTHLY", "QUARTERLY", "YEARLY")])
    region_opts = options_html([(r, r) for r in DATA_REGIONS], "EU-CENTRAL")
    workspace_opts = options_html(
        [(w, w.replace("_", " ").title()) for w in WORKSPACE_TYPES], blank=True
    )
    delivery_opts = options_html([(d, d.title()) for d in INVOICE_DELIVERY], "EMAIL")
    today = app.ctx.clock.today().isoformat()
    picker_rows = []
    for kind, label in (("partner", "Partner"), ("customer", "Customer")):
        picker_rows.append(
            f'<div class="field"><label>{label}</label>'
            f'<span data-testid="{kind}-summary">(none)</span> '
            f'<button type="button" data-testid="pick-{kind}">Select {label.lower()}</button>'
            f'<button type="button" data-testid="{kind}-change" hidden>Change</button></div>'
        )
    body = f"""
<ol aria-label="Steps" data-testid="wizard-steps"><li aria-current="step">Parties</li>
<li>Price</li><li>Confirm</li></ol>
<section data-testid="step-parties" aria-label="Parties">
{"".join(picker_rows)}
<div class="field"><label for="customer-number">Customer number</label>
<input id="customer-number" data-testid="customer-number" autocomplete="off">
<button type="button" data-testid="customer-number-lookup">Look up</button>
<span data-testid="customer-number-status" role="status"></span></div>
<div data-testid="step-verification" aria-label="Verification" hidden>
<p>The selected customer is not verified yet.</p>
<button type="button" data-testid="screening-run">{e(app.ctx.settings["screening_label"])}</button>
<span data-testid="screening-status" role="status"></span></div>
<div class="field"><label>Billing address</label>
<span data-testid="address-block" data-country="">-</span></div>
<div class="field"><label for="plan">Plan</label>
<select id="plan" data-testid="plan-select">{plan_opts}</select></div>
<div class="field"><label for="frequency">Frequency</label>
<select id="frequency" data-testid="frequency-select">{freq_opts}</select></div>
<div class="field"><label for="start">Start date</label>
<input id="start" type="date" data-testid="start-date" value="{today}"></div>
<div class="field"><label for="region">Data region</label>
<select id="region" data-testid="data-region-select">{region_opts}</select></div>
<div class="field"><label for="workspace">Workspace type</label>
<select id="workspace" data-testid="workspace-select">{workspace_opts}</select></div>
<div class="field"><label for="delivery">Invoice delivery</label>
<select id="delivery" data-testid="delivery-select">{delivery_opts}</select></div>
<p class="inline-error" role="alert" data-testid="step-error"></p>
<button type="button" data-testid="parties-submit">Next</button>
</section>{PICKER_HTML}"""
    return layout(app, "New subscription", body, script=PICKER_JS + WIZARD_JS)


QUOTE_JS = r"""
const page = demo.data();
function priceBody() {
  return {
    net_amount: document.querySelector('[data-testid="price-net"]').value,
    tax_rate: document.querySelector('[data-testid="price-tax-rate"]').value,
    commission_percentage: document.querySelector('[data-testid="price-commission"]').value,
  };
}
async function calculate() {
  demo.clearError(); demo.fieldErrors({});
  try {
    const quote = await demo.call('/api/quotes/calculate', {method: 'POST', json: priceBody()});
    const show = (id, value) => {
      document.querySelector('[data-testid="' + id + '"]').textContent = value.toFixed(2);
    };
    show('price-tax-amount', quote.tax_amount); show('price-gross', quote.gross_amount);
    document.querySelector('[data-testid="price-commission-amount"]').textContent =
      quote.commission_amount.toFixed(2);
    document.querySelector('[data-testid="price-net-normalised"]').textContent =
      quote.net_amount.toFixed(2);
    return quote;
  } catch (err) {
    if (err.body && err.body.fields) demo.fieldErrors(err.body.fields);
    throw err;
  }
}
document.querySelector('[data-testid="price-calculate"]').addEventListener('click',
  () => calculate().catch(() => {}));
document.querySelector('[data-testid="price-submit"]').addEventListener('click', async () => {
  try {
    const quote = await calculate();
    if (document.querySelector('[data-testid="activate-immediately"]').checked) {
      await demo.call('/api/subscriptions/' + page.id, {method: 'PATCH', json: {
        monthly_net: quote.net_amount, tax_rate: quote.tax_rate,
        commission_percentage: quote.commission_percentage, activate_immediately: true}});
      await demo.call('/api/subscriptions/' + page.id + '/actions/confirm', {method: 'POST'});
      window.location.href = '/ui/subscriptions/' + page.id;
      return;
    }
    await demo.call('/api/subscriptions/' + page.id + '/actions/submit',
                    {method: 'POST', json: priceBody()});
    window.location.href = '/ui/subscriptions/' + page.id + '/step/confirm';
  } catch (err) { /* errors shown */ }
});
"""


def step_quote(app: DemoApp, req: Request, id: str) -> Response:
    try:
        view = app.subs.view(id)
    except NotFound:
        return not_found(app, req)
    price = view["price"]
    body = f"""
<ol aria-label="Steps" data-testid="wizard-steps"><li>Parties</li>
<li aria-current="step">Price</li><li>Confirm</li></ol>
<section data-testid="step-quote" aria-label="Price" data-state="{e(view["status"])}">
<div class="field"><label for="net">Net amount per month</label>
<input id="net" data-testid="price-net" inputmode="decimal" value="{price["monthly_net"]:.2f}">
<span class="inline-error" data-field-error data-testid="error-net_amount" role="alert"></span>
</div>
<div class="field"><label for="tax">Tax rate (%)</label>
<input id="tax" data-testid="price-tax-rate" value="{price["tax_rate"]:g}">
<span class="inline-error" data-field-error data-testid="error-tax_rate" role="alert"></span></div>
<div class="field"><label for="commission">Commission (%)</label>
<input id="commission" data-testid="price-commission"
 value="{(view.get("commission_percentage") or 0):g}">
<span class="inline-error" data-field-error data-testid="error-commission_percentage"
 role="alert"></span></div>
<button type="button" data-testid="price-calculate">Calculate</button>
<dl><dt>Net</dt><dd data-testid="price-net-normalised"></dd>
<dt>Tax</dt><dd data-testid="price-tax-amount"></dd>
<dt>Gross</dt><dd data-testid="price-gross"></dd>
<dt>Commission</dt><dd data-testid="price-commission-amount"></dd></dl>
<div class="field"><label><input type="checkbox" data-testid="activate-immediately">
Activate immediately</label></div>
<button type="button" data-testid="price-submit" data-action="submit">Generate quote</button>
</section>"""
    return layout(
        app, f"Price for {view['number']}", body, page_data={"id": view["id"]}, script=QUOTE_JS
    )


CONFIRM_JS = r"""
const page = demo.data();
document.querySelector('[data-action="confirm"]').addEventListener('click', async () => {
  try {
    await demo.call('/api/subscriptions/' + page.id + '/actions/confirm', {method: 'POST'});
    window.location.href = '/ui/subscriptions/' + page.id;
  } catch (err) { /* banner shows it */ }
});
"""


def step_confirm(app: DemoApp, req: Request, id: str) -> Response:
    try:
        view = app.subs.view(id)
    except NotFound:
        return not_found(app, req)
    body = f"""
<ol aria-label="Steps" data-testid="wizard-steps"><li>Parties</li><li>Price</li>
<li aria-current="step">Confirm</li></ol>
<section data-testid="step-confirm" aria-label="Confirm">
<p>Subscription <strong data-testid="subscription-number">{e(view["number"])}</strong> for
<span data-testid="holder-name">{e((view["holder"] or {}).get("name"))}</span>,
<span data-testid="monthly-gross">{view["price"]["monthly_gross"]:.2f}</span> per month.</p>
<p><span class="pill" data-testid="status-pill" data-state="{e(view["status"])}">
{e(view["status"])}</span></p>
<button type="button" data-action="confirm" data-testid="action-confirm">Confirm</button>
</section>"""
    return layout(
        app, f"Confirm {view['number']}", body, page_data={"id": view["id"]}, script=CONFIRM_JS
    )


DRAFT_JS = r"""
const page = demo.data();
const q = sel => document.querySelector('[data-testid="' + sel + '"]');
async function patch(body) {
  const draft = await demo.call('/api/drafts/' + page.id, {method: 'PATCH', json: body});
  const status = q('autosave-status'); if (status) status.textContent = 'Saved';
  return draft;
}
function moved(id) { const n = q(id); return n && n.value !== n.defaultValue; }
function picked(id) { const n = q(id); return n && n.value !== (n.dataset.initial || ''); }
function changeBody() {
  const changes = {};
  if (moved('draft-monthly-net')) changes.monthly_net = Number(q('draft-monthly-net').value);
  if (moved('draft-spend-cap')) changes.spend_cap = Number(q('draft-spend-cap').value);
  document.querySelectorAll('[data-addon]').forEach(box => {
    if (box.checked !== box.defaultChecked) {
      changes.addons = changes.addons || {}; changes.addons[box.dataset.addon] = box.checked;
    }
  });
  if (picked('draft-data-region')) changes.data_region = q('draft-data-region').value;
  if (picked('draft-workspace-type'))
    changes.workspace_type = q('draft-workspace-type').value || null;
  if (picked('draft-invoice-delivery'))
    changes.invoice_delivery = q('draft-invoice-delivery').value;
  if (moved('draft-po-reference')) changes.po_reference = q('draft-po-reference').value;
  if (moved('draft-contact-name') || moved('draft-contact-email')) {
    changes.billing_contact = {name: q('draft-contact-name').value,
                               email: q('draft-contact-email').value};
  }
  const note = q('draft-add-note');
  if (note && note.value) changes.add_invoice_notes = [{text: note.value}];
  const label = q('draft-add-label');
  if (label && label.value) changes.add_labels = [label.value];
  if (page.owner) changes.customer_id = page.owner;
  return changes;
}
function closureBody() {
  return {effective_date: q('draft-effective-date').value, reason: q('draft-reason').value};
}
async function confirmDraft() {
  demo.clearError();
  try {
    const body = {};
    const eff = q('draft-effective-date');
    if (eff) body.effective_date = eff.value;
    if (page.kind === 'change') body.changes = changeBody();
    if (page.kind === 'cancel') Object.assign(body, closureBody());
    if (page.kind === 'note') {
      body.title = q('note-title').value; body.description = q('note-description').value;
    }
    if (page.kind === 'renew') body.index_percent = Number(q('renewal-index').value);
    await patch(body);
    await demo.call('/api/drafts/' + page.id + '/actions/confirm', {method: 'POST'});
    window.location.href = '/ui/subscriptions/' + page.subscription_id;
  } catch (err) {
    if (err.body && err.body.fields) demo.fieldErrors(err.body.fields);
  }
}
function closureChecks() {
  let ok = true;
  const picked = q('draft-reason').value;
  q('error-precondition').textContent = ''; q('error-effective_date').textContent = '';
  if (!q('draft-effective-date').value) {
    q('error-effective_date').textContent = 'Choose the date the subscription ends.'; ok = false;
  }
  if (!q('closure-precondition').checked) {
    q('error-precondition').textContent = 'Tick the box to confirm the closure.'; ok = false;
  }
  if (!ok) {
    // the form is drawn again with its errors; the chosen reason is put back
    const reason = q('draft-reason');
    reason.innerHTML = reason.innerHTML;
    reason.value = page.reset_select_on_error ? reason.options[0].value : picked;
  }
  return ok;
}
document.querySelectorAll('[data-action^="confirm_"], [data-testid="note-submit"]').forEach(b =>
  b.addEventListener('click', () => {
    if (page.kind === 'cancel' && !closureChecks()) return;
    confirmDraft();
  }));
const discard = document.querySelector('[data-action="discard_draft"]');
if (discard) discard.addEventListener('click', async () => {
  await demo.call('/api/drafts/' + page.id + '/actions/discard', {method: 'POST'});
  window.location.href = '/ui/subscriptions/' + page.subscription_id;
});
const transfer = q('owner-reselect');
if (transfer) transfer.addEventListener('click', () => picker.open('customers', item => {
  page.owner = item.id; q('owner-summary').textContent = item.number + ' ' + item.name;
  q('ownership-step').hidden = item.id === page.current_owner;
}));
const accept = q('ownership-accept');
if (accept) accept.addEventListener('click', async () => {
  await patch({changes: changeBody()});
  const draft = await demo.call('/api/drafts/' + page.id + '/actions/accept-ownership',
                                {method: 'POST'});
  q('ownership-status').textContent = draft.ownership_accepted ? 'Accepted' : 'Waiting';
});
let timer = null;
['note-title', 'note-description'].forEach(id => {
  const node = q(id);
  if (node) node.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(() => patch({title: q('note-title').value,
                                    description: q('note-description').value}), 250);
  });
});
async function showRenewal() {
  const draft = await demo.call('/api/drafts/' + page.id);
  const p = draft.preview;
  q('renewal-old-term-end').value = p.old_term_end;
  q('renewal-new-term-end').value = p.new_term_end;
  q('renewal-current-price').textContent = p.current_monthly_net.toFixed(2);
  q('renewal-new-price').textContent = p.new_monthly_net.toFixed(2);
  if (draft.index_percent !== null && draft.index_percent !== undefined)
    q('renewal-index').value = draft.index_percent;
  const step = new URLSearchParams(window.location.search).get('step') || 'indexation';
  q('step-indexation').hidden = step !== 'indexation';
  q('step-price').hidden = step !== 'price';
}
if (page.kind === 'renew') {
  q('renewal-index').addEventListener('change', async () => {
    const draft = await patch({index_percent: Number(q('renewal-index').value)});
    q('renewal-new-price').textContent = draft.preview.new_monthly_net.toFixed(2);
  });
  q('renewal-next').addEventListener('click', () => {
    if (!q('renewal-new-term-end').value) {
      q('error-new-term-end').textContent = 'New term end is required.'; return;
    }
    window.location.href = '/ui/drafts/' + page.id + '?step=price';
  });
  q('renewal-back').addEventListener('click', () => history.back());
  window.addEventListener('pageshow', showRenewal);
}
"""


def _select(testid: str, values: list[tuple[str, str]], current: Any, *, blank: bool) -> str:
    return (
        f'<select id="{testid}" data-testid="{testid}" data-initial="{e(current or "")}">'
        f"{options_html(values, current, blank=blank)}</select>"
    )


def draft_page(app: DemoApp, req: Request, id: str) -> Response:
    try:
        draft = app.subs.draft_view(id)
    except NotFound:
        return not_found(app, req)
    sub = app.subs.view(draft["subscription_id"])
    kind = draft["kind"]
    eff = (
        f'<div class="field"><label for="draft-effective-date">Effective date</label>'
        f'<input id="draft-effective-date" type="date" data-testid="draft-effective-date" '
        f'value="{e(draft["effective_date"])}">'
        '<span class="inline-error" data-field-error data-testid="error-effective_date" '
        'role="alert"></span></div>'
    )
    discard = '<button type="button" data-action="discard_draft">Discard draft</button>'
    if kind == "change":
        addon_boxes = "".join(
            f'<label><input type="checkbox" data-addon="{k}" data-testid="addon-{k}"'
            f"{' checked' if sub['addons'].get(k) else ''}> {e(label)}</label> "
            for k, label in ADDONS.items()
        )
        notes = "".join(
            f'<li data-testid="invoice-note-row" data-number="{n["number"]}">{n["number"]}. '
            f"{e(n['text'])} (v{n['version']})</li>"
            for n in sub.get("invoice_notes") or []
        )
        contact = sub.get("billing_contact") or {}
        holder = sub["holder"] or {}
        regions = _select(
            "draft-data-region", [(r, r) for r in DATA_REGIONS], sub["data_region"], blank=False
        )
        workspaces = _select(
            "draft-workspace-type",
            [(w, w.replace("_", " ").title()) for w in WORKSPACE_TYPES],
            sub.get("workspace_type"),
            blank=True,
        )
        deliveries = _select(
            "draft-invoice-delivery",
            [(d, d.title()) for d in INVOICE_DELIVERY],
            sub["invoice_delivery"],
            blank=False,
        )
        body = f"""{eff}
<section data-testid="step-details" aria-label="Details">
<div class="field"><label for="m">Net amount per month</label>
<input id="m" data-testid="draft-monthly-net" value="{sub["price"]["monthly_net"]:.2f}"></div>
<div class="field"><label for="f">Payment frequency</label>
<input id="f" data-testid="draft-frequency" value="{e(sub["frequency"])}" readonly
 aria-readonly="true"></div>
<div class="field"><label for="cur">Currency</label>
<input id="cur" data-testid="draft-currency" value="{e(sub["currency"])}" readonly
 aria-readonly="true"></div>
<div class="field" role="group" aria-label="Add-ons">{addon_boxes}</div>
<div class="field"><label for="draft-data-region">Data region</label>{regions}</div>
<div class="field"><label for="draft-workspace-type">Workspace type</label>{workspaces}</div>
<div class="field"><label for="draft-invoice-delivery">Invoice delivery</label>{deliveries}
<span class="inline-error" data-field-error data-testid="error-invoice_delivery" role="alert">
</span></div>
<div class="field"><label for="sc">Monthly spend cap</label>
<input id="sc" data-testid="draft-spend-cap" value="{sub.get("spend_cap") or 0:.2f}"></div>
<div class="field"><label for="po">PO reference</label>
<input id="po" data-testid="draft-po-reference" value="{e(sub.get("po_reference"))}">
<span class="inline-error" data-field-error data-testid="error-po_reference" role="alert">
</span></div>
<div class="field" role="group" aria-label="Billing contact">
<label for="cn">Billing contact name</label>
<input id="cn" data-testid="draft-contact-name" value="{e(contact.get("name"))}">
<label for="ce">Billing contact e-mail</label>
<input id="ce" data-testid="draft-contact-email" value="{e(contact.get("email"))}"></div>
<ul data-testid="invoice-notes-list">{notes}</ul>
<div class="field"><label for="t">Add invoice note</label>
<input id="t" data-testid="draft-add-note"></div>
<div class="field"><label for="lb">Add label</label><select id="lb" data-testid="draft-add-label">
{options_html(list(LABELS.items()), blank=True)}</select></div>
<div class="field"><label>Owner</label>
<span data-testid="owner-summary">{e(holder.get("number"))} {e(holder.get("name"))}</span>
<button type="button" data-testid="owner-reselect">Transfer to another customer</button></div>
<div data-testid="ownership-step" hidden>
<p>The new owner has to accept the subscription before the change can be confirmed.</p>
<button type="button" data-testid="ownership-accept">Record acceptance</button>
<span data-testid="ownership-status" role="status">Waiting</span></div>
</section>
<button type="button" data-action="confirm_change" data-testid="action-confirm_change">
Confirm change</button> {discard}{PICKER_HTML}"""
    elif kind == "cancel":
        reasons = [(r, r.replace("_", " ").capitalize()) for r in CLOSURE_REASONS]
        body = f"""{eff}
<section data-testid="step-details" aria-label="End subscription">
<div class="field"><label for="draft-reason">Reason</label>
<select id="draft-reason" data-testid="draft-reason">
{options_html(reasons, draft["reason"], blank=True)}</select>
<span class="inline-error" data-field-error data-testid="error-reason" role="alert"></span></div>
<div class="field"><label><input type="checkbox" data-testid="closure-precondition">
The subscription may end on this date</label>
<span class="inline-error" data-testid="error-precondition" role="alert"></span></div>
<span class="inline-error" data-field-error data-testid="error-payout_account" role="alert">
</span>
</section>
<button type="button" data-action="confirm_cancel" data-testid="action-confirm_cancel">
Confirm closure</button> {discard}"""
    elif kind == "renew":
        body = f"""
<section data-testid="step-indexation" aria-label="Indexation">
<div class="field"><label for="ote">Current term end</label>
<input id="ote" data-testid="renewal-old-term-end" readonly aria-readonly="true"></div>
<div class="field"><label for="nte">New term end</label>
<input id="nte" data-testid="renewal-new-term-end" readonly aria-readonly="true" required>
<span class="inline-error" data-testid="error-new-term-end" role="alert"></span></div>
<div class="field"><label>Current net per month</label>
<span data-testid="renewal-current-price"></span></div>
<div class="field"><label for="idx">Index (%)</label>
<input id="idx" data-testid="renewal-index" inputmode="decimal"
 value="{app.ctx.settings["index_percent"]}"></div>
<div class="field"><label>New net per month</label><span data-testid="renewal-new-price"></span>
</div>
<button type="button" data-testid="renewal-next">Next</button>
</section>
<section data-testid="step-price" aria-label="Price" hidden>
<button type="button" data-testid="renewal-back">Back</button>
<button type="button" data-action="confirm_renew" data-testid="action-confirm_renew">
Confirm renewal</button></section> {discard}"""
    else:
        body = f"""
<section data-testid="step-note" aria-label="Note">
<div class="field"><label for="nt">Title</label>
<input id="nt" data-testid="note-title" value="{e(draft.get("title"))}">
<span class="inline-error" data-field-error data-testid="error-title" role="alert"></span></div>
<div class="field"><label for="nd">Description</label>
<textarea id="nd" data-testid="note-description">{e(draft.get("description"))}</textarea></div>
<span data-testid="autosave-status" role="status"></span>
<button type="button" data-testid="note-submit">Save note</button></section>"""
    data = {
        "id": draft["id"],
        "kind": kind,
        "subscription_id": sub["id"],
        "owner": None,
        "current_owner": sub.get("customer_id"),
        "reset_select_on_error": app.ctx.fault("form_resets_select_after_error"),
    }
    steps = "".join(f'<li data-testid="draft-step">{e(s)}</li>' for s in draft["steps"])
    label = {"cancel": "End subscription"}.get(kind, kind.title())
    header = (
        f'<p>Draft <span data-testid="draft-kind">{e(kind)}</span> for '
        f'<a href="/ui/subscriptions/{sub["id"]}" data-testid="subscription-number">'
        f'{e(sub["number"])}</a> - <span data-testid="draft-status">{e(draft["status"])}</span>'
        f'</p><ol aria-label="Steps">{steps}</ol>'
    )
    script = (PICKER_JS if kind == "change" else "") + DRAFT_JS
    return layout(app, f"{label} {sub['number']}", header + body, page_data=data, script=script)


# ============================================================================
# customers
# ============================================================================


def customer_list(app: DemoApp, req: Request) -> Response:
    rows = app.customers.list()
    body = (
        '<p><a data-testid="customer-new" href="/ui/customers/new">New customer</a></p>'
        + "".join(
            f'<div data-testid="customer-row" data-id="{c["id"]}"><a data-testid="customer-link"'
            f' href="/ui/customers/{c["id"]}/edit">{e(c["number"])}</a> {e(c["display_name"])} '
            f'<span data-testid="customer-status">{e(c["status"])}</span></div>'
            for c in rows
        )
    )
    return layout(app, "Customers", body)


CUSTOMER_JS = r"""
const page = demo.data();
const q = sel => document.querySelector('[data-testid="' + sel + '"]');
function collect() {
  const address = {};
  document.querySelectorAll('[data-address-key]').forEach(n => {
    address[n.dataset.addressKey] = n.value;
  });
  return {
    type: q('customer-type').value,
    display_name: q('customer-display-name').value,
    email: q('customer-email').value || null,
    phone: q('customer-phone').value || null,
    billing_address: address,
    payout_account: q('customer-payout-account').value || null,
  };
}
q('customer-save').addEventListener('click', async () => {
  demo.clearError(); demo.fieldErrors({});
  try {
    const saved = page.customer
      ? await demo.call('/api/customers/' + page.customer.id, {method: 'PATCH', json: collect()})
      : await demo.call('/api/customers', {method: 'POST', json: collect()});
    q('save-status').textContent = 'Saved ' + saved.number;
    q('save-status').dataset.customerId = saved.id;
  } catch (err) {
    if (err.body && err.body.fields) demo.fieldErrors(err.body.fields);
  }
});
"""

ADDRESS_FIELDS = (
    ("line1", "Address line 1"),
    ("line2", "Address line 2"),
    ("postal_code", "Postal code"),
    ("city", "City"),
    ("country", "Country"),
)


def customer_form(app: DemoApp, req: Request, id: str | None = None) -> Response:
    customer = None
    if id is not None:
        try:
            customer = app.customers.get(id)
        except NotFound:
            return not_found(app, req)
    c = customer or {}
    address = c.get("billing_address") or {"country": HOME_COUNTRY}

    def err(key: str) -> str:
        return (
            f'<span class="inline-error" data-field-error data-testid="error-{key}" '
            'role="alert"></span>'
        )

    address_rows = "".join(
        f'<div class="field"><label for="ad-{key}">{label}</label>'
        f'<input id="ad-{key}" data-testid="address-{key}" data-address-key="{key}" '
        f'value="{e(address.get(key))}">{err("billing_address-" + key)}</div>'
        for key, label in ADDRESS_FIELDS
    )
    types = [(t, t.title()) for t in CUSTOMER_TYPES]
    body = f"""
<form data-testid="customer-form" aria-label="Customer" onsubmit="return false">
<div class="field"><label for="ct">Type</label><select id="ct" data-testid="customer-type">
{options_html(types, c.get("type"))}</select>{err("type")}</div>
<div class="field"><label for="dn">Display name</label>
<input id="dn" data-testid="customer-display-name" value="{e(c.get("display_name"))}">
{err("display_name")}</div>
<div class="field"><label for="em">E-mail</label>
<input id="em" data-testid="customer-email" value="{e(c.get("email"))}">{err("email")}</div>
<div class="field"><label for="ph">Phone</label>
<input id="ph" data-testid="customer-phone" value="{e(c.get("phone"))}">{err("phone")}</div>
<fieldset data-testid="billing-address"><legend>Billing address</legend>{address_rows}</fieldset>
<div class="field"><label for="pa">Payout account</label>
<input id="pa" data-testid="customer-payout-account" value="{e(c.get("payout_account"))}">
{err("payout_account")}</div>
<p><button type="button" data-testid="customer-save">Save</button>
<span data-testid="save-status" role="status"></span></p>
</form>"""
    title = f"Customer {c['number']}" if customer else "New customer"
    return layout(app, title, body, page_data={"customer": customer}, script=CUSTOMER_JS)


def customer_edit(app: DemoApp, req: Request, id: str) -> Response:
    return customer_form(app, req, id)


def customer_new(app: DemoApp, req: Request) -> Response:
    return customer_form(app, req)


# ============================================================================
# tasks
# ============================================================================

TASKS_JS = r"""
document.querySelectorAll('[data-testid="task-menu"]').forEach(button => {
  button.addEventListener('click', () => {
    const menu = button.nextElementSibling; menu.hidden = !menu.hidden;
    button.setAttribute('aria-expanded', String(!menu.hidden));
  });
});
document.querySelector('[data-testid="task-create"]').addEventListener('click', async () => {
  demo.clearError();
  const sub = document.querySelector('[data-testid="task-new-subscription"]').value;
  const body = {title: document.querySelector('[data-testid="task-new-title"]').value,
                target_at: document.querySelector('[data-testid="task-new-target"]').value || null};
  if (sub) body.subscription_id = Number(sub);
  try {
    const task = await demo.call('/api/tasks', {method: 'POST', json: body});
    document.querySelector('[data-testid="task-create-status"]').textContent = task.state;
    window.location.reload();
  } catch (err) { /* banner */ }
});
"""


def local_parts(instant: str, zone: str) -> tuple[str, str]:
    """A UTC instant shown as local date and HH:mm in the configured zone."""
    moment = datetime.fromisoformat(instant.replace("Z", "+00:00")).astimezone(ZoneInfo(zone))
    return moment.strftime("%Y-%m-%d"), moment.strftime("%H:%M")


def tasks_page(app: DemoApp, req: Request) -> Response:
    zone = app.ctx.settings["ui_timezone"]
    rows = []
    for t in app.tasks.list():
        day, hm = local_parts(t["target_at"], zone)
        open_link = (
            f'<a role="menuitem" data-testid="task-open" '
            f'href="/ui/subscriptions/{t["subscription_id"]}?version={t["version"]}">Open</a>'
            if t["subscription_id"]
            else '<span role="menuitem" aria-disabled="true">Open</span>'
        )
        rows.append(
            f'<tr data-testid="task-row" data-id="{t["id"]}">'
            f'<td data-testid="task-title">{e(t["title"])}</td>'
            f'<td data-testid="task-subscription">{e(t["subscription_number"])}</td>'
            f'<td data-testid="task-date">{day}</td><td data-testid="task-time">{hm}</td>'
            f'<td data-testid="task-state">{e(t["state"])}</td>'
            f'<td><button type="button" data-testid="task-menu" aria-haspopup="menu" '
            f'aria-expanded="false">...</button><div role="menu" hidden>{open_link}</div></td></tr>'
        )
    body = f"""
<p>Times shown in <span data-testid="task-zone">{e(zone)}</span>.</p>
<table data-testid="tasks-table"><thead><tr><th>Title</th><th>Subscription</th><th>Date</th>
<th>Time</th><th>State</th><th></th></tr></thead><tbody>{"".join(rows)}</tbody></table>
<section aria-label="New task" class="card">
<label>Title <input data-testid="task-new-title"></label>
<label>Subscription id <input data-testid="task-new-subscription"></label>
<label>Target (UTC instant) <input data-testid="task-new-target"></label>
<button type="button" data-testid="task-create">Create task</button>
<span data-testid="task-create-status" role="status"></span></section>"""
    return layout(app, "Tasks", body, script=TASKS_JS)


# ============================================================================
# admin
# ============================================================================


def admin_index(app: DemoApp, req: Request) -> Response:
    cards = "".join(
        f'<div class="card" data-testid="admin-card-{key}" role="region" aria-label="{e(title)}">'
        f"<h2>{e(title)}</h2>"
        + "".join(
            f'<a data-testid="admin-link-{k}" href="/ui/admin/{k}">{e(label)}</a><br>'
            for k, label in links
        )
        + "</div>"
        for key, title, links in ADMIN_CARDS
    )
    return layout(app, "Admin", cards)


def _admin_crumbs(label: str) -> list[tuple[str, str | None]]:
    return [("Admin", "/ui/admin"), (label, None)]


def admin_archived_lists(app: DemoApp, req: Request) -> Response:
    body = '<p data-testid="archived-lists">No archived lists.</p>'
    return layout(app, "Archived lists", body, breadcrumbs=_admin_crumbs("Archived lists"))


def admin_simple(app: DemoApp, req: Request, section: str) -> Response:
    store = app.ctx.store
    specs = {
        "plans": (
            "Plans",
            store.find("plans"),
            [
                ("code", "Code"),
                ("name", "Name"),
                ("kind", "Kind"),
                ("base_monthly_net", "Base net / month"),
            ],
        ),
        "partners": ("Partners", store.find("partners"), [("number", "Number"), ("name", "Name")]),
        "jobs": (
            "Jobs",
            store.find("jobs"),
            [("id", "Id"), ("kind", "Kind"), ("state", "State"), ("due_date", "Due")],
        ),
        "users": ("Users", app.users.list(), [("username", "User"), ("roles", "Roles")]),
        "exports": (
            "Exports",
            store.find("exports"),
            [
                ("id", "Id"),
                ("kind", "Kind"),
                ("status", "Status"),
                ("attemptCount", "Attempts"),
                ("lastAttemptAt", "Last attempt"),
            ],
        ),
    }
    if section not in specs:
        return not_found(app, req)
    title, rows, cols = specs[section]
    return layout(app, title, table(rows, cols, section), breadcrumbs=_admin_crumbs(title))


ACCOUNTS_JS = r"""
document.querySelectorAll('[data-testid="account-menu"]').forEach(button => {
  button.addEventListener('click', () => {
    const menu = button.nextElementSibling; menu.hidden = !menu.hidden;
    button.setAttribute('aria-expanded', String(!menu.hidden));
  });
});
"""


def admin_accounts(app: DemoApp, req: Request) -> Response:
    rows = []
    for a in app.accounts.list():
        managed = bool(a["managed"])
        edit = (
            '<button role="menuitem" data-testid="account-edit" disabled aria-disabled="true">'
            "Edit</button>"
            if managed
            else f'<a role="menuitem" data-testid="account-edit" '
            f'href="/ui/admin/accounts/{a["id"]}/edit">Edit</a>'
        )
        rows.append(
            f'<tr data-testid="account-row" data-id="{a["id"]}" '
            f'data-managed="{str(managed).lower()}">'
            f'<td data-testid="account-number">{e(a["number"])}</td>'
            f'<td data-testid="account-name">{e(a["name"])}</td>'
            f'<td data-testid="account-managed">{"Managed" if managed else ""}</td>'
            f'<td><button type="button" data-testid="account-menu" aria-haspopup="menu" '
            f'aria-expanded="false">...</button><div role="menu" hidden>{edit}</div></td></tr>'
        )
    body = (
        '<table data-testid="accounts-table"><thead><tr><th>Number</th><th>Name</th><th></th>'
        f"<th></th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )
    return layout(
        app,
        "Ledger accounts",
        body,
        script=ACCOUNTS_JS,
        breadcrumbs=_admin_crumbs("Ledger accounts"),
    )


ACCOUNT_EDIT_JS = r"""
const page = demo.data();
document.querySelector('[data-testid="account-save"]').addEventListener('click', async () => {
  demo.clearError();
  try {
    const saved = await demo.call('/api/accounts/' + page.id, {method: 'PATCH',
      json: {name: document.querySelector('[data-testid="account-name-input"]').value}});
    document.querySelector('[data-testid="save-status"]').textContent = 'Saved ' + saved.name;
  } catch (err) { /* banner */ }
});
"""


def admin_account_edit(app: DemoApp, req: Request, id: str) -> Response:
    try:
        account = app.accounts.get(id)
    except NotFound:
        return not_found(app, req)
    body = f"""
<div class="field"><label for="an">Number</label><input id="an" data-testid="account-number-input"
 value="{e(account["number"])}" readonly aria-readonly="true"></div>
<div class="field"><label for="am">Name</label><input id="am" data-testid="account-name-input"
 value="{e(account["name"])}"></div>
<button type="button" data-testid="account-save">Save</button>
<span data-testid="save-status" role="status"></span>"""
    return layout(
        app,
        f"Edit account {account['number']}",
        body,
        page_data={"id": account["id"]},
        script=ACCOUNT_EDIT_JS,
        breadcrumbs=_admin_crumbs("Edit account"),
    )


INTEGRATIONS_JS = r"""
async function render() {
  const services = await demo.call('/api/integrations/services');
  const html = services.map(s => {
    const field = (param, optional) => (
      '<div class="field"><label for="' + s.key + '-' + param + '">' + param +
      (optional ? ' (optional)' : '') + '</label><input id="' + s.key + '-' + param +
      '" data-testid="integration-' + s.key + '-' + param + '" data-param="' + param + '"' +
      (optional ? '' : ' required aria-required="true"') + '>' +
      (optional ? '<span class="hint" data-testid="hint-' + s.key + '-' + param +
       '">Optional. Leave empty to use the default.</span>' : '') + '</div>');
    return '<form class="card" data-testid="integration-' + s.key + '" aria-label="' +
      demo.esc(s.name) + '" onsubmit="return false"><h2>' + demo.esc(s.name) + '</h2>' +
      s.params.map(p => field(p, false)).join('') +
      s.optionalParams.map(p => field(p, true)).join('') +
      '<button type="button" data-testid="integration-' + s.key + '-save" data-key="' + s.key +
      '">Save</button> <span role="status" data-testid="integration-' + s.key +
      '-status"></span></form>';
  }).join('');
  document.querySelector('[data-testid="integrations"]').innerHTML = html;
  document.querySelectorAll('[data-key]').forEach(button => button.addEventListener('click',
    async () => {
      demo.clearError();
      const form = button.closest('form'); const body = {};
      form.querySelectorAll('[data-param]').forEach(n => {
        if (n.value) body[n.dataset.param] = n.value;
      });
      try {
        await demo.call('/api/integrations/' + button.dataset.key, {method: 'POST', json: body});
        form.querySelector('[role="status"]').textContent = 'Saved';
      } catch (err) { /* banner */ }
    }));
}
render();
"""


def admin_integrations(app: DemoApp, req: Request) -> Response:
    return layout(
        app,
        "Integrations",
        '<div data-testid="integrations"></div>',
        script=INTEGRATIONS_JS,
        breadcrumbs=_admin_crumbs("Integrations"),
    )


SETTLEMENTS_JS = r"""
const page = demo.data();
const state = {tab: 'open', party: null};
const input = document.querySelector('[data-testid="party-filter"]');
const list = document.querySelector('[data-testid="party-options"]');
function narrow() {
  const text = input.value.toLowerCase();
  list.innerHTML = page.partners.filter(p => !text ||
      (p.number + ' ' + p.name).toLowerCase().includes(text))
    .map(p => '<li role="option" data-testid="party-option" data-id="' + p.id + '">' +
      demo.esc(p.number + ' ' + p.name) + '</li>').join('');
  list.hidden = false;
  list.querySelectorAll('[data-testid="party-option"]').forEach(option =>
    option.addEventListener('click', () => {
      state.party = option.dataset.id; input.value = option.textContent; list.hidden = true;
      load();
    }));
}
async function load() {
  if (!state.party) return;
  const rows = await demo.call('/api/settlements?party=' + encodeURIComponent(state.party) +
                               '&tab=' + state.tab);
  document.querySelector('[data-testid="settlements-table"] tbody').innerHTML = rows.map(r =>
    '<tr data-testid="settlement-row" data-id="' + r.id + '"><td>' + demo.esc(r.reference) +
    '</td><td>' + demo.esc(r.partner_number) + '</td><td>' + r.closing_balance.toFixed(2) +
    '</td><td>' + demo.esc(r.status) + '</td></tr>').join('');
}
input.addEventListener('input', narrow);
input.addEventListener('focus', narrow);
document.querySelectorAll('[role="tab"]').forEach(tab => tab.addEventListener('click', () => {
  document.querySelectorAll('[role="tab"]').forEach(t => t.setAttribute('aria-selected', 'false'));
  tab.setAttribute('aria-selected', 'true'); state.tab = tab.dataset.tab; load();
}));
"""


def admin_settlements(app: DemoApp, req: Request) -> Response:
    partners = [
        {"id": p["id"], "number": p["number"], "name": p["name"]}
        for p in app.ctx.store.find("partners")
    ]
    body = """
<div role="tablist" aria-label="Settlement status">
<button role="tab" aria-selected="true" data-tab="open" data-testid="tab-open">Open</button>
<button role="tab" aria-selected="false" data-tab="closed" data-testid="tab-closed">Closed</button>
</div>
<div class="field"><label for="party">Party</label>
<input id="party" data-testid="party-filter" autocomplete="off" role="combobox"
 aria-controls="party-options" aria-expanded="false">
<ul id="party-options" role="listbox" data-testid="party-options" hidden></ul></div>
<table data-testid="settlements-table"><thead><tr><th>Reference</th><th>Party</th>
<th>Closing balance</th><th>Status</th></tr></thead><tbody></tbody></table>"""
    return layout(
        app,
        "Settlements",
        body,
        page_data={"partners": partners},
        script=SETTLEMENTS_JS,
        breadcrumbs=_admin_crumbs("Settlements"),
    )


# ============================================================================
# registration
# ============================================================================


def register(router: Router) -> None:
    def add(method: str, path: str, handler: Any, summary: str) -> None:
        router.add(method, path, summary, "ui", api=False, produces="text/html")(handler)

    add("GET", "/", root, "Redirect to the dashboard")
    add("GET", "/favicon.ico", favicon, "No icon")
    add("GET", "/login", login_page, "Login form")
    add("POST", "/login", login_page, "Login submit")
    add("GET", "/logout", logout, "Log out")
    add("GET", "/ui", index, "Dashboard")
    add("GET", "/ui/subscriptions", subscription_list, "Subscription list")
    add("GET", "/ui/subscriptions/new", wizard, "New-subscription wizard")
    add("GET", "/ui/subscriptions/{id}", subscription_detail, "Subscription detail")
    add("GET", "/ui/subscriptions/{id}/step/quote", step_quote, "Price step")
    add("GET", "/ui/subscriptions/{id}/step/confirm", step_confirm, "Confirm step")
    add("GET", "/ui/drafts/{id}", draft_page, "Change / cancel / renew / note draft")
    add("GET", "/ui/customers", customer_list, "Customer list")
    add("GET", "/ui/customers/new", customer_new, "New customer form")
    add("GET", "/ui/customers/{id}/edit", customer_edit, "Edit customer form")
    add("GET", "/ui/tasks", tasks_page, "Tasks")
    add("GET", "/ui/admin", admin_index, "Admin cards")
    add("GET", "/ui/admin/accounts", admin_accounts, "Ledger accounts")
    add("GET", "/ui/admin/accounts/{id}/edit", admin_account_edit, "Edit ledger account")
    add("GET", "/ui/admin/integrations", admin_integrations, "Integrations")
    add("GET", "/ui/admin/settlements", admin_settlements, "Settlements")
    add("GET", "/ui/admin/archived-lists", admin_archived_lists, "Retired page, still reachable")
    add("GET", "/ui/admin/{section}", admin_simple, "Simple admin lists")
