# Platform roles and API tokens

Who may do what on the platform. Identity is **API tokens only**: no passwords, no
external identity provider. The code is `qaplatform/rbac.py` (roles, permissions, matrix)
and `qaplatform/auth/tokens.py` (issue, verify); users and role bindings live in
`qap_users` and `qap_role_bindings` (migration `0003_rbac.sql`).

## What it does

Four fixed roles, bound per project. The matrix is data, pinned by a test that spells
out the whole table, so widening a role is a visible diff:

| Permission | viewer | tester | lead | admin |
|---|:-:|:-:|:-:|:-:|
| `read` (runs, results, verdicts, traceability, history) | yes | yes | yes | yes |
| `push_results` (runs, results, JUnit, verdicts, evidence, finish) | | yes | yes | yes |
| `record_manual` (manual executions, exploratory sessions) | | yes | yes | yes |
| `trigger_run` (run requests) | | | yes | yes |
| `read_audit` (audit log, chain verification) | | | yes | yes |
| `manage_tokens`, `manage_members`, `manage_project` | | | | yes |

- **Enforced in one place.** Every `PlatformService` method names the permission it needs
  and passes the single scope check (`_scope` / `_write`), which checks project visibility
  first and the role second. The REST API, the UI and the CLI are adapters over the
  service, so they cannot disagree. A test reads every public service method's source and
  fails if one does not pass through that check; another calls each one with the strongest
  role that lacks its permission and junk arguments and expects a refusal, before any
  argument is looked at.
- **Tokens:** `qap_` + 32 random bytes, stored as a SHA-256 hash only, shown once, scoped to
  one project and a role, with a mandatory expiry (default 90 days, at most 366), a
  last-used timestamp (refreshed at most once a minute) and revocation.
- **Effective role = the lower of the token's role and the owner's binding**, recomputed on
  every request. Lowering a member's role lowers their existing tokens at once; removing a
  member revokes all their tokens in the project; a disabled user's tokens stop working.
  A token's role may not exceed its owner's binding (checked at issue).
- **Instance tokens:** the bootstrap admin (`python -m qaplatform init`) holds an instance
  token that is an admin token on every project. Only an instance admin can hold one.
- **Guard rails:** the last admin of a project cannot be demoted or removed. Unauthorised
  calls are refused before the body is validated, so a caller without the permission
  learns nothing about the payload rules.
- **Audited:** granting, removing, issuing and revoking are audit rows (`member.grant`,
  `member.revoke`, `token.issue`, `token.revoke`); the raw token never enters the log.

| Status | Meaning |
|---|---|
| 401 | missing, unknown, expired or revoked token; owner disabled or no longer bound to the project |
| 403 | the project is visible to the token but its effective role lacks the permission |
| 404 | the project is not visible to the token (existence is not disclosed) |

## How to use

```bash
python -m qaplatform init                        # default project + instance admin token
python -m qaplatform token create --user ci --role tester --label ci-main   # grants if needed
python -m qaplatform token list --project default
python -m qaplatform token revoke tok_1a2b3c...
```

REST (role `admin`):

| Method | Path | Body |
|---|---|---|
| GET | `/api/v1/me` | (any token) user, role, permissions, expiry |
| GET / POST | `/api/v1/projects/{p}/members` | `{"schema_version": "1.0", "user_id": "ci-bot", "role": "tester"}` |
| DELETE | `/api/v1/projects/{p}/members/{user_id}` | |
| GET / POST | `/api/v1/projects/{p}/tokens` | `{"schema_version": "1.0", "user_id": "ci-bot", "role": "tester", "label": "ci", "ttl_days": 30}` |
| DELETE | `/api/v1/projects/{p}/tokens/{token_id}` | |

CLI commands that change tokens or members run as `local-operator` (someone with the
database file already has full control); they go through the service, so they are audited
like API calls. Revoking an *instance* token is the one change with no project chain to
record it in.

## Configuration

None beyond the token's own `ttl_days`. Custom roles and an external identity provider
are deliberately out of scope until there is more than one human user per project.

## Example

A CI pipeline gets its own member and token; a person gets a lead token to trigger runs:

```bash
python -m qaplatform token create --user ci-main --role tester --label pipeline --ttl-days 30
python -m qaplatform token create --user alex --role lead --label laptop
```

## Honest limits

A leaked `tester` token can push fake results into its project until it expires or is
revoked. The role limit, the expiry and the audit log contain and attribute that; they do
not prevent it.
