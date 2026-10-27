# Feature reference

One page per area. Each section covers what the feature does, how to use it,
its configuration (environment variables and defaults) and a short example.

| Page | Area |
|---|---|
| [verdicts.md](verdicts.md) | Expected-red detectors, coverage verdicts, open-issue manifests, verdict records |
| [ci-workflow.md](ci-workflow.md) | Workflow shape lint, nightly switch, secret scan, manual-run inputs, per-environment credentials, external config binding, token-only lane, evidence publishing, failure notification |
| [assistant-config.md](assistant-config.md) | Protected-branch push guard (hook and deny list), path-scoped rules lint |
| [run-gate.md](run-gate.md) | Run trust gate, rerun-safe run ids, lanes, fail-fast, circuit breaker, read-only client |
| [api-http.md](api-http.md) | HTTP engine, typed responses, multipart/download, run tagging, safe versioned retry |
| [test-data.md](test-data.md) | Cleanup ledger, fixture-replay seeder, seed modes and targets, reuse pool, loaders, scenario manifest |
| [oracles.md](oracles.md) | Locale amounts/dates, PDF text, document and template integrity, day counts, reconciliation, data health |
| [config-env.md](config-env.md) | Config ladder, .env files, TEST_ENV, per-env profile, host-family check, .env template, run id and test-traffic tagging |
| [auth-session.md](auth-session.md) | Log in once and reuse the session, token expiry, session refresh, token grants (password, client credentials, injected token), secret lookup |
| [browser-evidence.md](browser-evidence.md) | Console/page/network evidence, trace and video retention, bounded teardown, failure bundle, version probe, gateway retry |
| [reporting.md](reporting.md) | Allure environment, executor, categories, per-step screenshots |
| [ui-components.md](ui-components.md) | Dropdowns, date and value entry with read-back, wizard submit, field reader, reference picker, selector priority model, submit outcome capture, test-id screen object |
| [healing.md](healing.md) | Locator store with heal proposals, heal event log, model client transport selection |
| [data-health.md](data-health.md) | Read-only data-health scan report (unverified column, creator split) and its entry point |
| [release-tooling.md](release-tooling.md) | Promotion diff between two version manifests, config preflight (records a new config would reject), hotfix merge-back report, release lead time |
| [demo-sut.md](demo-sut.md) | In-repo demo subscription service: lifecycle machine, fault switches, clock, fixtures |
| [generators.md](generators.md) | Workflow YAML model, state graph, spec writer, config-driven/path cases, zero-code factory, generate/convert CLI, trace-to-fixture |
| [demo-examples.md](demo-examples.md) | Demo examples index; generator examples |
| [demo-examples-cancellation.md](demo-examples-cancellation.md) | Cancellation examples: refund oracles, fault-switch detectors |
| [demo-examples-change.md](demo-examples-change.md) | Mid-term change examples: scenario manifest, customer changes, proration |
| [demo-examples-documents.md](demo-examples-documents.md) | Document examples: rendered PDFs, template structure, content detectors |
| [demo-examples-finance.md](demo-examples-finance.md) | Finance examples: invoices, ledger, settlements, commission rules |
| [demo-examples-lifecycle.md](demo-examples-lifecycle.md) | Lifecycle examples: seeding, creation, renewal, detectors |
| [demo-examples-messaging.md](demo-examples-messaging.md) | Outbound messaging: segment completeness, ordering, stored-once |
| [demo-examples-scheduler.md](demo-examples-scheduler.md) | Synchronous billing runner, scheduler probes, chain stall detector |
| [demo-examples-smoke.md](demo-examples-smoke.md) | Gateway routes, fix-scope delta, integrations, platform smoke, sequence numbers, role history |
| [results-store.md](results-store.md) | Results store (SQLite), migrations, controller-only recorder, evidence links, run bundle, REST handlers |
| [test-case-catalogue.md](test-case-catalogue.md) | Test case catalogue generated from code: identity and pins, fields, catalogue build/diff, case REST handlers |
| [case-versioning.md](case-versioning.md) | Revision hash per test function (git blob formula), file blob and commit per revision, revision ranges |
| [results-history-and-trends.md](results-history-and-trends.md) | Case and suite trends, run compare, unstable/slowest, flakiness on store history, results-history branch |
| [dashboards.md](dashboards.md) | Static HTML dashboard: overview, run, case, unstable, slowest, catalogue and search pages |
| [search.md](search.md) | Full-text search (SQLite FTS5, LIKE fallback) over cases, failures, doc sections and defects |
| [import-export.md](import-export.md) | JUnit XML import (any framework), CSV/JSON exports, TestRail-/Xray-style formatters and id mapping |
| [living-documentation.md](living-documentation.md) | Feature pages with live test status: front matter, directives, proven/failing/unproven/stale, docs-tests lint |
| [scheduling.md](scheduling.md) | Scheduled runs, run trigger, staleness check, dispatch adapter |
| [sharded-execution.md](sharded-execution.md) | Shard planning, shard plugin, merge and completeness check |
| [browser-matrix.md](browser-matrix.md) | Engine identity, per-engine results, engine-scoped known defects, matrix job |
| [synthetic-data.md](synthetic-data.md) | Constraint profiles, seeded valid/boundary/invalid generation, keyed masking, probe plugin |
| [contract-gate.md](contract-gate.md) | fail_on_breaking, OpenAPI diff gate, Pact v3 export, api-contract plugin |
| [db-testing.md](db-testing.md) | Read-only probes, statement guard, state windows with invariants, gated writer |
| [load-testing.md](load-testing.md) | k6 load engine, profiles and thresholds, metric rows, load-performance plugin |
| [accessibility.md](accessibility.md) | axe-core scans per route, rule-scoped known defects, detector pair, accessibility plugin |
| [visual-regression.md](visual-regression.md) | Committed per-platform baselines, pixel diff with threshold, approvals, visual plugin, Linux baseline job |
| [security-testing.md](security-testing.md) | gitleaks over history, bandit SAST, pip-audit on the lock file, expiring allow-list, security plugin |
| [mutation-testing.md](mutation-testing.md) | mutmut 3 per-package scope and thresholds, isolated copy, score gate, survivors, mutation plugin |
| [fault-injection.md](fault-injection.md) | Playwright and HTTP fault injection, faults fixture, not-exercised rule, breaker ignores injected faults, fault-injection plugin |
| [requirements-linking.md](requirements-linking.md) | `requirement` marker, REQ-n registry, id namespace vs defect ids, case-links file, case requirement REST handlers |
| [traceability-matrix.md](traceability-matrix.md) | Requirement statuses (UNCOVERED..PROVED) with precedence, artefact and store sources, matrix REST handlers |
| [test-plans.md](test-plans.md) | YAML test plans: selectors, `pytest --plan`, exit criteria met/unmet/no_evidence, plan REST handlers |
| [coverage-gaps.md](coverage-gaps.md) | Deterministic coverage gaps (OpenAPI, specs, Postman, requirements, workflow actions), `covers` marker, runtime endpoint hits, baseline ratchets |
| [generated-test-review.md](generated-test-review.md) | `draft` marker from every generator, approval by reviewed removal, drafts lane, review lint and queue |
| [release-verdict.md](release-verdict.md) | Strict release verdict (GO / GO_WITH_WAIVERS / NO_GO / NO_VERDICT), policy, waivers, inputs hash, release REST handlers |
| [defect-tracker.md](defect-tracker.md) | Tracker prefix map, GitHub Issues and file adapters, status snapshot into known_defect/still_reproduces, fingerprinted drafts, opt-in filing |
| [requirements-sync.md](requirements-sync.md) | Requirement sources (repo YAML, labelled GitHub issues), merge, snapshot, diff and orphaned links, bot-PR snapshot job |
| [pr-feedback.md](pr-feedback.md) | PR summary from run artefacts (integrity first, detectors, new flakes, requirement changes, new gaps, drafts), in-place comment, forks step summary only |
| [platform_api.md](platform_api.md) | Versioned REST API over the results store: push, server-side run trust, reads, run requests; consumer guide in ../integration_api.md |
| [platform_audit.md](platform_audit.md) | Append-only, hash-chained audit log written in the same transaction as every change; verify, head, export |
| [platform_rbac.md](platform_rbac.md) | Roles viewer/tester/lead/admin, permission matrix, API tokens (hash-only, expiring, revocable), members |
| [platform_tenancy.md](platform_tenancy.md) | Projects: project-scoped store, 404 across projects, archive = read-only |
| [platform_ui.md](platform_ui.md) | Server-rendered UI: runs, run detail with integrity verdict, requirements, history, audit, manual runner, sessions |
| [manual_execution.md](manual_execution.md) | Thin manual runner: spec cases, step results, pinned revisions, recorded vs proved |
| [exploratory_sessions.md](exploratory_sessions.md) | Exploratory sessions: charter, notes, debrief, timebox, Playwright capture; recorded, never proved |
