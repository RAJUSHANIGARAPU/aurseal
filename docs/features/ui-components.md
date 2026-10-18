# UI component helpers

Package: `ui/components/`, plus `core/base_page.py` and `core/locator_strategy.py`. Every helper locates by test
id, role or label, which is the framework's locator order. None of them uses raw
CSS or XPath, and none uses a fixed sleep.

## Ordered locator fallback

### What it does

`BasePage.resolve_first_present(*keys)` returns the first locator key that matches
anything, trying them **in the order given**. A single locator that combines the
alternatives resolves by *document order* instead, so a loose fallback that
appears higher on the page beats the exact match you listed first. When nothing
matches, it returns the last key's locator, so the caller's own "not present"
handling and error message still apply.

`BasePage` also accepts a `locator_file`. JSON entries can be of type `label`,
and can set `"regex": true` (case-insensitive by default; switch that off with
`"ignore_case": false`) on `role` names, `label`, `testid` and `text` values.

### Example

```json
{
  "order_total": {"type": "testid", "value": "order-total"},
  "order_total_by_label": {"type": "label", "value": "^total", "regex": true}
}
```

```python
total = self.resolve_first_present("order_total", "order_total_by_label")
```

## Exact test-id paths

### What it does

Apps that put a field path into `data-testid` (`form.customer.email`) often put
it on the field's helper elements too (`form.customer.email-error`). A substring
match can pick the error element and read its text back as the value.
`identifies(value, path)` accepts a value only if it **is** the path or **ends**
with it after a `.` or `-`. `by_path(page, path)` uses the same rule as a
`get_by_test_id` pattern.

```python
from ui.components.exact_attribute import by_path

by_path(page, "customer.email")   # matches form.customer.email, not ...email-error or ...emailAddress
```

## Field reading

### What it does

`read_field(page, path=..., label=...)` finds the field by **test id** first
(exact path), then by **exact label**. If the label matches more than one field,
it raises `AmbiguousFieldError` rather than picking by document order. It returns
a `FieldValue(raw, via, label)` where `via` is `test-id`, `label` or `absent`.

- Form controls are read with `input_value()`. Only a non-input element falls
  back to its text, with every word-bounded, case-insensitive copy of the label
  removed. An empty field therefore reads as `""` and never as its own label.
- `FieldValue.empty()` is also true when all that is left is a validation
  message. The phrases are matched on their wording (`DEFAULT_VALIDATION_PHRASES`:
  "is required", "is invalid", "must not be empty", ...). Pass your own list for
  other wording or languages.
- `field_is_read_only(page, ...)` checks `readonly`, `aria-readonly` and the
  disabled state, and treats a non-input element as static text. It returns
  `None` when the field is not on screen.

```python
from ui.components.field_reader import field_is_read_only, read_field

email = read_field(page, path="customer.email", label="Email")
assert not email.empty(), f"email is empty (read via {email.via})"
assert field_is_read_only(page, label="Customer number")
```

The string rules are available on their own in `ui/components/field_text.py`
(`strip_label_echo`, `carries_no_value`, `looks_like_validation_message`).

## Value entry with read-back

### What it does

`fill_and_verify(field, value)` clears the field with `ControlOrMeta+A` (on macOS,
`Control+A` moves the caret to the start of the line instead of selecting), types
the value one key at a time (input masks often only listen to key events), tabs
out, and reads the value back. If the field does not hold the value, it raises
`ValueNotLandedError` at that point. Without that check, a wrong value such as
`57.25100.00` (old and new run together) would only show up as wrong
arithmetic several screens later.

`fill_amount_and_verify` compares numerically with `parse_amount`, which reads
`1,234.56`, `1.234,56`, `57,25`, currency symbols, spaces and leading or
trailing minus signs. A single separator followed by exactly three digits is read
as a thousands separator. A value that cannot be read is never equal.

```python
from ui.components.value_entry import fill_amount_and_verify

fill_amount_and_verify(page.get_by_label("Amount"), "57.25", description="amount")
```

| Option | Default |
|---|---|
| `key_delay_ms` | 50 |
| `compare` | exact text (`text_equal`); `amounts_equal` for amounts |

## Locale date typing

### What it does

`type_date(field, date, locale)` types the date in the field's display format, one
key at a time, so the date picker updates its model. It then tabs out, closes the
calendar with Escape, and checks that the field holds the typed text.

| Locale | Format |
|---|---|
| `iso` (default) | `2026-03-09` |
| `en_US` | `03/09/2026` |
| `en_GB`, `fr_FR` | `09/03/2026` |
| `nl_NL` | `09-03-2026` |
| `de_DE` | `09.03.2026` |

```python
from datetime import date
from ui.components.date_entry import type_date

type_date(page.get_by_label("Start date"), date(2026, 3, 9), "en_GB")
```

## Select and autocomplete

### What it does

`select_by_label(page, label, option)` requires **exactly one** combobox with that
exact accessible name. A widget that is not a combobox cannot match, and two
matches raise an error. It opens the combobox with a dispatched click (floating
labels often sit on top of the trigger), picks the option by role and name, and
reads back the value the field shows. If the field does not show the option, it
presses Escape and tries again. After the last attempt it raises `DropdownError`
listing the options it saw.

```python
from ui.components.dropdown import select_by_label, select_option

select_by_label(page, "Billing period", "Yearly")
select_option(page, page.get_by_test_id("plan-picker"), "Pro", attempts=2)
```

## Wizard submit and settle waits

### What it does

`submit_step(page, button_name)` waits for the page to settle, clicks the
**visible** button with that name, and waits again. Hidden copies of the button
left behind by earlier steps are ignored. If the click fails, it logs the visible
buttons and alert texts, which usually explain why, and re-raises.
`settle(page, timeout_ms)` waits for network idle and returns `False`, with a
warning, instead of raising. `has_visible_alerts(page)` reports whether any
`role=alert` element is visible.

```python
from ui.components.wizard import has_visible_alerts, submit_step

submit_step(page, "Continue to payment")
assert not has_visible_alerts(page)
```

## Reference-dialog picker

### What it does

`pick(page, text)` chooses a row in the topmost `role=dialog`. It tries the
dialog's default list first, then types into the dialog's search box and looks
again. After the click it waits until the number of open dialogs has dropped,
which is when the field has been written. It returns `"default-list"` or
`"search"`. If the row is not found, it raises `ReferencePickError` listing the
rows it saw.

```python
from ui.components.reference_picker import pick

page.get_by_role("button", name="Choose customer").click()
pick(page, "Acme Ltd", search_term="Acme")
```

## Backend response capture

### What it does

`ResponseCapture(page, url_match)` keeps the latest successful (2xx) JSON
response of a named backend call, so a test can compare what the screen shows
with what the server returned. `url_match` can be a substring, a regex or a
predicate. Paths are `/`-separated (`items/0/price`), and amounts come back as
`Decimal`, rounded half-up to two places.

- `amount_at(path)` returns the amount at that path;
- `first_amount(paths)` returns the first candidate path that holds an amount, and the path itself;
- `find_amount_path(target)` shows **which** field carries the value on screen.

```python
from ui.components.response_capture import ResponseCapture
from ui.components.value_entry import parse_amount

with ResponseCapture(page, "/quote") as quotes:
    submit_step(page, "Calculate")
shown = parse_amount(read_field(page, label="Total").raw)
assert quotes.amount_at("totals/gross") == shown, quotes.find_amount_path(shown)
```

## Hosted-login page object

### What it does

`UniversalLoginPage` drives a hosted OAuth/OIDC login page:

- **identifier-first** or single-screen: it clicks submit after the username only
  when the password field is not shown yet;
- only **visible** fields are filled, which skips hidden decoy fields;
- it picks an optional **organisation** by button or link name;
- it treats the login as finished when the **URL** matches the app;
- `navigate_with_retry` retries the first navigation with a growing backoff.

Locators are in `ui/components/locators/universal_login.json` as label and role
patterns. A login page with different wording only needs a JSON change.

```python
from ui.components.universal_login import UniversalLoginPage

UniversalLoginPage(page).login(
    "https://app.example.test/",
    "tester@example.test",
    password,
    app_url="https://app.example.test/",
    organization="Example Org",
)
```

Pair it with `login_once` to log in only once per run; see [auth-session.md](auth-session.md).

## Selector priority model

### What it does

`core/locator_strategy.py` encodes the selector priority as one ordered enum,
`LocatorStrategy`, instead of a sentence repeated in prompts and reviews. Most to
least durable:

| Rank | `type` | Registry entry | Renders to |
|---|---|---|---|
| 0 | `data` | `{"attribute": "data-key", "value": "save"}` | `[data-key="save"]` |
| 1 | `testid` | `{"value": "save-button"}` | `get_by_test_id` |
| 2 | `aria_label` | `{"value": "Save order"}` | `[aria-label="Save order"]` |
| 3 | `label` | `{"value": "Email"}` | `get_by_label` |
| 4 | `role` | `{"role": "button", "name": "Save"}` | `get_by_role` |
| 5 | `placeholder` | `{"value": "Search"}` | `get_by_placeholder` |
| 6 | `text` | `{"value": "Save"}` | `get_by_text` |
| 7 | `css` | `{"value": "form > button"}` | `locator` |

`regex: true` (and `ignore_case`, `exact`, `level`) work as before.

One vocabulary, three users:

- `BasePage.resolve(key)` renders through `render(page, entry)`, so the page
  objects accept every type above (the new ones are `data` and `aria_label`);
- the UI healer's prompt lists the same order (`priority_prompt()`), and it checks
  every suggested locator with `shape_error(entry)` — the check `render` uses — so
  a heal can never write an entry the page objects cannot resolve;
- `lint_registry(registry)` flags entries that cannot be rendered, and entries
  that use a lower-ranked strategy while their own `"attributes"` metadata records
  a higher-ranked one. An offline test lints every committed
  `ui/**/locators/*.json`.

### How to use it

```python
from core.locator_strategy import lint_registry, rank, ranked, render

render(page, {"type": "data", "attribute": "data-key", "value": "save"}).click()
best = ranked(candidate_entries)[0]
```

Record what you saw in the DOM so the lint can push the entry up the ladder:

```json
"save_button": {
  "type": "css", "value": "form button.primary",
  "attributes": {"data-testid": "save-order"}
}
```

```
save_button: css (rank 7) while testid (rank 1) is available
```

### Configuration

None.

## Submit outcome on every surface

### What it does

`ui/components/submit_outcome.py` submits a form step and records every place its
answer can arrive, so a validation assertion holds whether the message comes back
in the 4xx body, inline at the field, or only as "the form did not move on":

- `status` and `body` of the submit call (matched by URL, optionally by method),
  or `status=None` when no such call was made within the timeout — client-side
  validation blocked it, which is itself a finding;
- `inline_errors` and `alerts` that **appeared** because of this submit (texts
  already on screen are not counted);
- `advanced`, from a check you pass (for example "the next step's heading is visible").

`outcome.signals()` hands this to `oracles.rejection.RejectionSignals`, so the
decision reuses the rejection classifier and its guards. `outcome.mentions(text)`
searches all surfaces at once.

`discard_draft_if_present(page, menu_button, discard_item)` is the best-effort UI
clean-up for an abandoned form: open the menu, choose the discard item when both
exist, optionally confirm; it returns False instead of raising when they do not.
Prefer an API delete when the system offers one.

### How to use it

```python
from ui.components.submit_outcome import submit_and_capture

outcome = submit_and_capture(
    page, "Next", "/api/person/submit",
    advanced=lambda p: p.get_by_role("heading", name="Address").is_visible(),
)
assert outcome.rejected, outcome.describe()
assert outcome.mentions("date of birth"), outcome.describe()
```

### Configuration

| Argument | Default | Meaning |
|---|---|---|
| `method` | `POST` | method of the submit call; `None` = any |
| `inline_errors` | `[data-field-error], .field-error, [role='alert'][data-field]` | where field messages render |
| `alerts` | `[role='alert']:not([data-field])` | page-level messages |
| `timeout_ms` | `15000` | wait for the submit call |
| `settle_ms` | `8000` | best-effort network-idle wait afterwards |

## Test-id screen object

### What it does

`ui/components/screen.py` is one small object for a back-office screen whose
every hook is a test id (`data-testid` by default, or any `data-*` attribute the
front end uses) or an ARIA role. It saves writing a page object per screen when
a screen ships and is checked on its own.

- `open(route, ready_test_id)` navigates and waits for the ready element. It
  **fails fast on a login redirect**: as soon as the URL contains a login marker
  it raises `LoginRedirectError` naming where it landed, instead of timing out
  on a missing element. `ScreenNotReadyError` covers the plain timeout.
- Reading: `count`, `text`, `read_input` → `EntryState(value, visible, enabled,
  editable, required, invalid)` (works on the input or a wrapper around it),
  `read_control` → `WidgetState(text, visible, enabled, checked)`, `row_count`,
  `wait_for_rows`, `row_menu_items(table, row, menu)` (opens the row's menu,
  reads the visible items, closes it), `menu_options(trigger)`.
- Acting: `type_into`, `select_option`, `choose_menu_option`, `open_tab`,
  `click`, `press_escape`.

### How to use it

```python
from ui.components.screen import Screen

screen = Screen(page, base_url).open("admin/messages", "message-table")
assert screen.wait_for_rows("message-table") > 0
assert screen.row_menu_items("message-table", 0, "row-menu") == ["Resend", "Download"]
assert screen.read_input("sender-address").read_only
```

### Configuration

| Argument | Default | Meaning |
|---|---|---|
| `test_id_attribute` | `data-testid` | attribute the app's test hooks use |
| `login_markers` | `/login`, `/signin`, `/authorize` | URL fragments that mean "redirected to log in" |
| `row_selector` | `tbody tr` | rows inside a table element |

## Native select and native date input

### What it does

A native `<select>` renders its options outside the page, so `select_option`
above cannot open it. `dropdown.select_native(select, option, by="label")` uses
Playwright's own select call, then reads the selected option's text (or value
with `by="value"`) back; a choice that did not land is retried and then raised
with the options on offer.

A native `<input type="date">` keeps its value in ISO whatever it displays, and its
segments follow the browser locale, so typing keystrokes lands digits in the
wrong segment. `date_entry.set_native_date(field, date)` sets the ISO value and
raises `ValueNotLandedError` unless the field reads it back.

```python
from datetime import date
from ui.components.date_entry import set_native_date
from ui.components.dropdown import select_native

select_native(page.get_by_label("Frequency"), "Yearly")
set_native_date(page.get_by_test_id("start-date"), date(2026, 4, 15))
```

| Option | Default |
|---|---|
| `attempts` | 3 |
| `by` | `label` (`value` also accepted) |

## Resending a submit safely

### What it does

`resend_rule.server_did_not_act(sent, status)` is true only when a resend cannot
duplicate a write: the request was never sent, or it was refused with a 4xx. A
2xx did act, and a 5xx, a gateway timeout or no answer may have acted, so those
are never resent. `submit_tracking_sent(page, click, url, ...)` wraps
`submit_and_capture` and also records whether a matching request left the
browser; its `TrackedSubmit.may_resend` applies the rule. `next_choice(preferred,
offered, tried)` picks the next preferred value the screen offers for a retry
with a different value after a refusal.

```python
from ui.components.resend_rule import submit_tracking_sent

first = submit_tracking_sent(page, lambda: page.get_by_test_id("save").click(), "/api/orders")
if not first.outcome.advanced and first.may_resend:
    submit_tracking_sent(page, lambda: page.get_by_test_id("save").click(), "/api/orders")
```

| `sent` | status | `may_resend` |
|---|---|---|
| no | none | yes |
| yes | 4xx | yes |
| yes | 2xx / 5xx / none | no |


## Never-shown watch

### What it does

`ui/components/absence_watch.py` records whether a phrase ever appeared in the page
body at any moment of a flow, not just when the test looks. Install it before driving
the flow; it re-arms on every new document and keeps its sightings in
`sessionStorage`, so reloads and same-origin navigations do not lose them.

Pair every absence assertion with a positive control on the same watch: a recorder
that failed to arm sees nothing, which an absence check alone would read as a pass.
Watch the label of the control that would act (a confirm button) rather than a
heading that may also sit in a progress bar or a hidden template.

### Example

```python
from ui.components.absence_watch import AbsenceWatch

watch = AbsenceWatch(page, ["Review order", "Skip payment"]).install()
checkout.complete()
watch.assert_seen("Review order", "every checkout shows the review step")
watch.assert_never_seen("Skip payment", "a paid plan must never offer to skip payment")
```
