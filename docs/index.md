# testkeel

An open-source test automation kit: scaffold a complete, reviewable test project in under
a minute, check it for leaked names and personal data, and turn result files into a
report.

This repository holds two things, and this site documents both:

| Part | What it is | Start here |
|---|---|---|
| **The kit** | The installable `testkeel` package: the `testkeel` command, its project templates, result importers, the HTML report and the leak gate | [Kit overview](kit/index.md) |
| **The framework** | The pytest + Playwright framework the kit grew out of, with its CI gates, lanes and result tooling | [Goals](vision.md), [What runs where](whats-live.md) |

Live example reports, published from the nightly run of the two example projects:

- [web-ui-saucedemo](reports/web-ui-saucedemo/)
- [api-rest-restful-booker](reports/api-rest-restful-booker/)
