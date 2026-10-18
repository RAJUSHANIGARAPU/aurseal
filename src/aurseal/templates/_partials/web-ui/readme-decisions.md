| Page objects plus components _(confirm)_ | Screenplay; plain functions | Locators live in one place per screen; a reviewer reads a test as user intent | One more layer to navigate |
| Locators by role, label or test id _(confirm)_ | CSS and XPath chains | Survive markup refactors and match what a user perceives | Needs accessible markup or test ids on the target |
| Web-first assertions only, no sleeps _(confirm)_ | Explicit waits, `sleep` | `expect` retries until the condition holds, so slow is not flaky | A wrong expectation fails after the timeout, not at once |
| Fresh browser context per test, run with `-n auto` _(confirm)_ | Shared logged-in browser | No test sees another test's state, so order and parallelism do not matter | Each test pays for its own setup |
| Reruns only per test, with a written reason _(confirm)_ | Global `--reruns` | A retry is evidence about one known cause, not a blanket that hides new ones | A genuinely flaky test fails until someone looks at it |
