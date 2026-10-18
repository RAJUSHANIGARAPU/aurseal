---
seed: false
feature: subscription
---

# Subscription workflow

Generated from `subscription.machine.yaml` by `python -m generators.cli spec`. Regenerate it instead of editing it.

## States and actions

### `draft` - Draft (initial)

- `submit` (Submit for payment) -> `pending_payment` - operations: compute_price, issue_order_summary
- `activate` (Activate immediately) -> `active` - when activate_immediately == true - operations: schedule_transactions, issue_account_opened

### `pending_payment` - Pending payment

- `confirm` (Confirm) -> `active` - operations: schedule_transactions, issue_account_opened

### `active` - Active

- `change` (Change) -> `changing` - operations: open_change_draft
- `cancel` (End subscription) -> `changing` - operations: open_cancel_draft
- `renew` (Renew) -> `changing` - when term_end within renewal window and every period before it booked - operations: open_renew_draft
- `note` (Add note) -> `active` - operations: open_note_draft

### `changing` - Change in progress

- `confirm_change` (Confirm change) -> `active` - when draft.kind == 'change' - operations: write_version, book_change_transaction, issue_plan_update_notice
- `confirm_cancel` (Confirm closure) -> `cancelled` - when draft.kind == 'cancel' - operations: write_version, book_credit, issue_closure_notice
- `confirm_renew` (Confirm renewal) -> `renewed` - when draft.kind == 'renew' - operations: write_version, index_price, schedule_renewal_term, issue_term_extension_notice
- `discard_draft` (Discard draft) -> `active` - operations: discard_draft

### `renewed` - Renewed

- `change` (Change) -> `changing` - operations: open_change_draft
- `cancel` (End subscription) -> `changing` - operations: open_cancel_draft
- `renew` (Renew) -> `changing` - when term_end within renewal window and every period before it booked - operations: open_renew_draft
- `note` (Add note) -> `renewed` - operations: open_note_draft

### `cancelled` - Cancelled (terminal)

- no actions

### `suspended` - Suspended (UNREACHABLE)

- `resume` (Resume) -> `active` - operations: reactivate

## Automatic transitions

- none

## Fields

| Field | Type | Required | Constraints |
|---|---|---|---|
| `customer_id` | reference | yes | reference to `customers` |
| `partner_id` | reference | yes | reference to `partners` |
| `plan_id` | reference | yes | reference to `plans` |
| `frequency` | choice | yes | one of `MONTHLY`, `QUARTERLY`, `YEARLY` |
| `start_date` | date | yes | - |
| `data_region` | choice | yes | one of `EU-CENTRAL`, `EU-NORTH`, `AP-SOUTH` |
| `workspace_type` | choice | no | one of `TEAM_SPACE`, `SANDBOX`, `TRAINING` |
| `invoice_delivery` | choice | yes | one of `EMAIL`, `PORTAL` |
| `monthly_net` | amount | no | min 1.0; max 10000.0 |
| `spend_cap` | amount | no | min 0 |
| `po_reference` | text | no | pattern `^[A-Za-z0-9 ./-]{1,35}$` |
| `activate_immediately` | boolean | no | - |

## Reachability

- reachable from draft: draft, pending_payment, active, changing, cancelled, renewed
- unreachable: suspended
- transitions no path walks: none

```text
subscription (initial: draft)
[draft]           --submit--> [pending_payment]
                  --activate--> [active]  if activate_immediately == true
[pending_payment] --confirm--> [active]
[active]          --change--> [changing]
                  --cancel--> [changing]
                  --renew--> [changing]
                  --note--> [active]
[changing]        --confirm_change--> [active]  if draft.kind == 'change'
                  --confirm_cancel--> [cancelled]  if draft.kind == 'cancel'
                  --confirm_renew--> [renewed]  if draft.kind == 'renew'
                  --discard_draft--> [active]
[renewed]         --change--> [changing]
                  --cancel--> [changing]
                  --renew--> [changing]
                  --note--> [renewed]
[cancelled]       (terminal)
[suspended]       --resume--> [active]
unreachable: suspended
```

---

## Scenario create a subscription

### Preconditions
- No subscription exists for this test

### Steps
1. Open the create screen for a subscription
2. Fill the required fields: `customer_id`, `partner_id`, `plan_id`, `frequency`, `start_date`, `data_region`, `invoice_delivery`
3. Save

### Expected
- The new subscription is shown in state `draft` ([data-state="draft"])

### Tags
`subscription` `smoke`

---

## Scenario submit from draft

### Preconditions
- A subscription in state `draft` ([data-state="draft"])

### Steps
1. Click the `[data-action="submit"]` element (Submit for payment)

### Expected
- The subscription is shown in state `pending_payment` ([data-state="pending_payment"])

### Tags
`subscription` `regression`

---

## Scenario activate from draft

### Preconditions
- A subscription in state `draft` ([data-state="draft"])
- Condition holds: activate_immediately == true

### Steps
1. Click the `[data-action="activate"]` element (Activate immediately)

### Expected
- The subscription is shown in state `active` ([data-state="active"])

### Tags
`subscription` `regression`

---

## Scenario confirm from pending_payment

### Preconditions
- A subscription in state `pending_payment` ([data-state="pending_payment"])

### Steps
1. Click the `[data-action="confirm"]` element (Confirm)

### Expected
- The subscription is shown in state `active` ([data-state="active"])

### Tags
`subscription` `regression`

---

## Scenario change from active

### Preconditions
- A subscription in state `active` ([data-state="active"])

### Steps
1. Click the `[data-action="change"]` element (Change)

### Expected
- The subscription is shown in state `changing` ([data-state="changing"])

### Tags
`subscription` `regression`

---

## Scenario cancel from active

### Preconditions
- A subscription in state `active` ([data-state="active"])

### Steps
1. Click the `[data-action="cancel"]` element (End subscription)

### Expected
- The subscription is shown in state `changing` ([data-state="changing"])

### Tags
`subscription` `regression`

---

## Scenario renew from active

### Preconditions
- A subscription in state `active` ([data-state="active"])
- Condition holds: term_end within renewal window and every period before it booked

### Steps
1. Click the `[data-action="renew"]` element (Renew)

### Expected
- The subscription is shown in state `changing` ([data-state="changing"])

### Tags
`subscription` `regression`

---

## Scenario note from active

### Preconditions
- A subscription in state `active` ([data-state="active"])

### Steps
1. Click the `[data-action="note"]` element (Add note)

### Expected
- The subscription is shown in state `active` ([data-state="active"])

### Tags
`subscription` `regression`

---

## Scenario confirm_change from changing

### Preconditions
- A subscription in state `changing` ([data-state="changing"])
- Condition holds: draft.kind == 'change'

### Steps
1. Click the `[data-action="confirm_change"]` element (Confirm change)

### Expected
- The subscription is shown in state `active` ([data-state="active"])

### Tags
`subscription` `regression`

---

## Scenario confirm_cancel from changing

### Preconditions
- A subscription in state `changing` ([data-state="changing"])
- Condition holds: draft.kind == 'cancel'

### Steps
1. Click the `[data-action="confirm_cancel"]` element (Confirm closure)

### Expected
- The subscription is shown in state `cancelled` ([data-state="cancelled"])

### Tags
`subscription` `regression`

---

## Scenario confirm_renew from changing

### Preconditions
- A subscription in state `changing` ([data-state="changing"])
- Condition holds: draft.kind == 'renew'

### Steps
1. Click the `[data-action="confirm_renew"]` element (Confirm renewal)

### Expected
- The subscription is shown in state `renewed` ([data-state="renewed"])

### Tags
`subscription` `regression`

---

## Scenario discard_draft from changing

### Preconditions
- A subscription in state `changing` ([data-state="changing"])

### Steps
1. Click the `[data-action="discard_draft"]` element (Discard draft)

### Expected
- The subscription is shown in state `active` ([data-state="active"])

### Tags
`subscription` `regression`

---

## Scenario change from renewed

### Preconditions
- A subscription in state `renewed` ([data-state="renewed"])

### Steps
1. Click the `[data-action="change"]` element (Change)

### Expected
- The subscription is shown in state `changing` ([data-state="changing"])

### Tags
`subscription` `regression`

---

## Scenario cancel from renewed

### Preconditions
- A subscription in state `renewed` ([data-state="renewed"])

### Steps
1. Click the `[data-action="cancel"]` element (End subscription)

### Expected
- The subscription is shown in state `changing` ([data-state="changing"])

### Tags
`subscription` `regression`

---

## Scenario renew from renewed

### Preconditions
- A subscription in state `renewed` ([data-state="renewed"])
- Condition holds: term_end within renewal window and every period before it booked

### Steps
1. Click the `[data-action="renew"]` element (Renew)

### Expected
- The subscription is shown in state `changing` ([data-state="changing"])

### Tags
`subscription` `regression`

---

## Scenario note from renewed

### Preconditions
- A subscription in state `renewed` ([data-state="renewed"])

### Steps
1. Click the `[data-action="note"]` element (Add note)

### Expected
- The subscription is shown in state `renewed` ([data-state="renewed"])

### Tags
`subscription` `regression`

---

## Scenario field validation for a subscription

### Preconditions
- The create screen for a subscription is open

### Steps
1. Leave `customer_id` empty
2. Leave `partner_id` empty
3. Leave `plan_id` empty
4. Leave `frequency` empty
5. Send a `frequency` value outside MONTHLY, QUARTERLY, YEARLY
6. Leave `start_date` empty
7. Leave `data_region` empty
8. Send a `data_region` value outside EU-CENTRAL, EU-NORTH, AP-SOUTH
9. Send a `workspace_type` value outside TEAM_SPACE, SANDBOX, TRAINING
10. Leave `invoice_delivery` empty
11. Send a `invoice_delivery` value outside EMAIL, PORTAL
12. Enter a `po_reference` value that does not match `^[A-Za-z0-9 ./-]{1,35}$`
13. Save

### Expected
- `customer_id` is reported as required
- `partner_id` is reported as required
- `plan_id` is reported as required
- `frequency` is reported as required
- `frequency` is rejected as not one of the choices
- `start_date` is reported as required
- `data_region` is reported as required
- `data_region` is rejected as not one of the choices
- `workspace_type` is rejected as not one of the choices
- `invoice_delivery` is reported as required
- `invoice_delivery` is rejected as not one of the choices
- `po_reference` is rejected for its format
- No subscription is created

### Tags
`subscription` `negative`

---

## Scenario happy path draft->pending_payment->active->changing->active

### Preconditions
- A new subscription in state `draft`

### Steps
1. Click `[data-action="submit"]`
2. Click `[data-action="confirm"]`
3. Click `[data-action="change"]`
4. Click `[data-action="confirm_change"]`

### Expected
- `[data-state="pending_payment"]` is shown after each step
- `[data-state="active"]` is shown after each step
- `[data-state="changing"]` is shown after each step
- `[data-state="active"]` is shown after each step

### Tags
`subscription` `smoke` `happy_path`
