# Examples

Two projects under `examples/`, each generated from a template and then written for a
public practice target. They show what a finished assignment looks like: the skeleton
from the template, unchanged, and the tests and page objects or services written by hand.

| Example | Template | Target | Live report |
|---|---|---|---|
| [`web-ui-saucedemo`](../../examples/web-ui-saucedemo/README.md) | `web-ui` | Sign-in, inventory, cart and checkout on the Sauce Demo shop | [report](../reports/web-ui-saucedemo/) |
| [`api-rest-restful-booker`](../../examples/api-rest-restful-booker/README.md) | `api-rest` | Authentication and booking create, read, update and delete on the Restful Booker API | [report](../reports/api-rest-restful-booker/) |

## How the reports are produced

- **Nightly, live.** `examples-nightly.yml` runs both suites against the real targets
  every night and keeps each pytest-html report as a build artifact. A failure opens or
  comments on one `live-drift: <example>` issue; it never fails a build of this
  repository, because a third-party outage is not a defect here.
- **Published.** After every nightly run, and on every push to `main`, the docs workflow
  (`pages.yml`) takes the reports from the latest completed nightly run and publishes
  them under `reports/` on this site. When no nightly report is available (artifacts are kept for 7 days), the
  page says so instead of showing a stale or empty report.
- **Never on a pull request.** The live suites never run on a pull request. What a pull
  request does check is that each example's template-owned files still match a fresh
  render (see below).

## Run one yourself

```bash
cd examples/api-rest-restful-booker
make test-offline   # no network: client, contract and model tests on a mock transport
make test           # the live suite against the public API
```

Each example's own README lists its decisions and trade-offs, scope, and known limits.

## Kept in step with the templates

Each example has a `.scaffold-manifest`: the files that came from the template unchanged.
The package tests render the template again and fail when any of those files differ, so
an example cannot drift from the template it claims to come from.
