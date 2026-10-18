# Platform projects (multi-tenancy)

The tenant unit is the **project**: a code base or product line. One database holds every
project; isolation is enforced in one layer and tested. The code is the `projects` table
(core migration), `qaplatform/store.py` (`ScopedStore`) and the scope check in
`qaplatform/service.py`.

## What it does

- **`project_id` on every row** of every results and platform table, from the first
  migration. External ids (`external_id`, `test_id`, requirement keys, ticket keys) are
  unique *within* a project; the platform issues its own run ids, so two projects can both
  send `external_id: "ci-1"` and never collide.
- **One choke point.** Every service method resolves a `ScopedStore` through `_scope`,
  which checks visibility (the token's project, or an instance token) and then the role.
  A `ScopedStore` is bound to one project when it is created: none of its methods takes a
  project argument, and every named query it runs filters on `:project_id`. A cross-project
  read is therefore not expressible through it. Tests pin all three properties: no scoped
  method has a project parameter, every file in `qaplatform/queries/` filters on
  `project_id = :project_id`, and the unscoped `PlatformStore` surface is an explicit
  allowlist (plumbing, tokens, users, the project registry).
- **Existence is not disclosed.** A token of project A gets 404, not 403, on every route of
  project B; a test walks the whole route table to prove it. Even an instance admin cannot
  read B's run through A's URL.
- **Projects:** created by an instance admin (the creator becomes the project's admin and
  the creation is the first row of the project's audit chain); renamed, given settings,
  archived or unarchived by a project admin. An **archived** project is read-only: reads
  work, every write answers 409.
- **Single-project installs never see any of this:** `init` creates `default`, and the CLI
  and client default to it.

## How to use

```bash
python -m qaplatform project create shop --name "Shop"     # as the local operator
python -m qaplatform project list
python -m qaplatform token create --project shop --user ci-shop --role tester
```

REST: `GET /api/v1/projects`, `POST /api/v1/projects` (instance admin),
`GET /api/v1/projects/{p}`, `PATCH /api/v1/projects/{p}` with
`{"schema_version": "1.0", "archived": true}`.

## Configuration

`QAP_PROJECT` (client side, default `default`) or `--project` on CLI commands.
Project keys: lowercase letters, digits and `-`, 2 to 40 characters.

## Example

Two teams on one server, each with its own CI token; neither can see the other's runs:

```bash
python -m qaplatform project create payments
python -m qaplatform project create search
python -m qaplatform token create --project payments --user ci-payments --role tester
python -m qaplatform token create --project search --user ci-search --role tester
```

## Honest limits

Row-level scoping is only as strong as its single choke point: a future report that
bypasses `ScopedStore` with raw SQL would bypass it too; the source tests and review are the
guard. There is no level above projects (organisations), no schema- or database-per-tenant
isolation, and cross-project dashboards would need an explicit admin-only aggregate read.
