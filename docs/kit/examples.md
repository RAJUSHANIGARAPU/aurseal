# Examples

Six projects under `examples/`, each generated from a template and then written for a
public practice target. They show what a finished assignment looks like: the skeleton
from the template, unchanged, and the tests and page objects or services written by hand.

| Example | Template | Target | Live report |
|---|---|---|---|
| [`web-ui-saucedemo`](../../examples/web-ui-saucedemo/README.md) | `web-ui` | Sign-in, inventory, cart and checkout on the Sauce Demo shop | [report](../reports/web-ui-saucedemo/) |
| [`api-rest-restful-booker`](../../examples/api-rest-restful-booker/README.md) | `api-rest` | Authentication and booking create, read, update and delete on the Restful Booker API | [report](../reports/api-rest-restful-booker/) |
| [`graphql-countries`](../../examples/graphql-countries/README.md) | `api-graphql` | Country lookups, request validation errors and schema drift on the Countries GraphQL API | [report](../reports/graphql-countries/) |
| [`a11y-w3c-bad`](../../examples/a11y-w3c-bad/README.md) | `a11y` | WCAG 2.1 AA scans of the W3C Before and After Demonstration; the inaccessible version is the positive control | [report](../reports/a11y-w3c-bad/) |
| [`perf-k6-quickpizza`](../../examples/perf-k6-quickpizza/README.md) | `perf-k6` | Smoke on the public QuickPizza demo; load and stress only against a local QuickPizza | build artifact |
| [`perf-locust-quickpizza`](../../examples/perf-locust-quickpizza/README.md) | `perf-locust` | The same QuickPizza traffic and SLOs in Locust, so the two tools read side by side | build artifact |

There is no `mobile`, `fullstack` or `strategy` example yet: a nightly mobile run needs an
emulator, no public practice target serves both a UI and its API, and a document set has
no live run to show.

## Two lanes: local and live

Every example runs in two places, and the reports say which.

| Example | Local target (every pull request) | Live target (nightly) |
|---|---|---|
| `web-ui-saucedemo` | Sauce Labs' `sample-app-web`, the source of the shop, built from a pinned commit | <https://www.saucedemo.com> |
| `api-rest-restful-booker` | The image the API's author publishes, pinned by digest | <https://restful-booker.herokuapp.com> |
| `graphql-countries` | The API built from its upstream source at a pinned commit; the schema drift test skips, because the snapshot was recorded from the live endpoint | <https://countries.trevorblades.com> |
| `a11y-w3c-bad` | **Not the demo**: the example's own fixtures served at the demo's page paths; the tests that assert on the demo itself are left out | <https://www.w3.org/WAI/demos/bad/> |
| `perf-k6-quickpizza` | Smoke profile against `quickpizza-local`, k6 in a container | Smoke on the public instance; load against `quickpizza-local` |
| `perf-locust-quickpizza` | Smoke profile and SLO check against `quickpizza-local` | Load against `quickpizza-local` |

- **Local, on every pull request.** `examples-pr.yml` runs `make test-local` for each
  example when a pull request touches `examples/` or the templates. The target runs in
  Docker on the runner and is removed afterwards; `test-local` refuses to start if any
  base URL in its environment points anywhere but `localhost` or a Compose service. The
  reports are kept for 7 days as `example-<name>-local` build artifacts and are not
  published on this site. A green local run shows the tests work; it says nothing about
  whether the public site still behaves the same.
- **Live, nightly.** `examples-nightly.yml` runs every suite against its real target
  every night and keeps its reports as a build artifact. The k6 example runs only its
  smoke profile against the public QuickPizza instance (one virtual user for 30 seconds,
  the size its README allows); both load examples run their load profile against a
  QuickPizza container on the runner, so no load ever leaves it. Their k6 and Locust
  summaries stay build artifacts; this site publishes the pytest-html reports only. A
  failure opens or comments on one `live-drift: <example>` issue; it never fails a build
  of this repository, because a third-party outage is not a defect here.
- **Published.** After every nightly run, and on every push to `main`, the docs workflow
  (`pages.yml`) takes the reports from the latest completed nightly run and publishes
  them under `reports/` on this site. When no nightly report is available (artifacts are
  kept for 7 days), the page says so instead of showing a stale or empty report. Only
  live reports are published.

## Run one yourself

```bash
cd examples/api-rest-restful-booker
make test-offline   # no network: client, contract and model tests on a mock transport
make test-local     # the whole suite against a local container (needs Docker), then removes it
make test           # the live suite against the public API
```

Each example's own README lists its decisions and trade-offs, scope, and known limits.

## Kept in step with the templates

Each example has a `.scaffold-manifest`: the files that came from the template unchanged.
The package tests render the template again and fail when any of those files differ, so
an example cannot drift from the template it claims to come from.
