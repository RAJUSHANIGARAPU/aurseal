# Goals

**What it is.** An open-source Python test automation framework, built on pytest and
Playwright, for web interfaces and HTTP APIs. Pointing it at a new web application should
take a base URL and credentials in the environment configuration, nothing more.

**Design rule.** The core assumes no particular company, product, component library or
data model. Whatever is specific to one application belongs in the test project that uses
the framework, not in the framework.

**Direction.** Take human effort out of the test lifecycle: write tests from
specifications and recordings, repair broken selectors, diagnose failures and report on
suite health. Every automated decision is logged so a person can review it and undo it.

**Principles.**

- Quick to adopt: configuration over code, sensible defaults per environment.
- Additive: a new capability never breaks a test that already passes.
- Stack-agnostic: patterns that hold for any web stack, not one front-end library.
- Auditable and overridable: automation leaves a record and a human has the last word.
- Honest reporting: a signal that was not measured is reported as not measured, never as
  zero.

**Status.** The current feature list is in the [README](../README.md#3-core-capabilities).
