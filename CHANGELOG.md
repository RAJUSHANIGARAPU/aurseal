# Changelog

All notable changes to the `aurseal` package are recorded here. The format follows
[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/) and the package follows
[Semantic Versioning](https://semver.org/); what the version number promises is in
[docs/kit/releasing.md](docs/kit/releasing.md).

## [Unreleased]

### Added

- `aurseal --version` prints the installed version and exits.
- Releases are published to PyPI with trusted publishing when a `aurseal-v<version>`
  tag is pushed; a manual run of the publish workflow rehearses on TestPyPI.

### Changed

- Renamed testkeel → aurseal. The package, the import path and the console script are
  now `aurseal` (`pip install aurseal`, `import aurseal`, `aurseal new-assignment`), and
  the repository moved to <https://github.com/RAJUSHANIGARAPU/aurseal> (the old URL
  redirects). Entry-point groups are `aurseal.importers`, `aurseal.result_types` and
  `aurseal.reporters`: a plugin registered under a `testkeel.*` group is no longer found.
  The pytest plugin is `-p aurseal` with the option `--aurseal-out`. Release tags are
  `aurseal-v<version>` (`testkeel-v0.1.0` stays as it is). Configuration lives in
  `~/.config/aurseal/`.
- The version is written once, in `src/aurseal/__init__.py`; `pyproject.toml` reads it
  from there.

### Deprecated

- `~/.config/testkeel/denylist.txt` and `~/.config/testkeel/allow-hosts.txt` are still
  read when the `aurseal` file is absent, and `assignment update` still reads the templates
  of a `testkeel-v*` tag and accepts a project whose answers record the old repository URL.
  These fallbacks are removed in the release after the next one.

## [0.1.0] - 2026-10-18

### Added

- `testkeel new-assignment`: render a project from a template, leak-gate it, lock and
  self-check it, then write it. Types: `web-ui`, `api-rest`, `api-graphql`, `a11y`,
  `strategy`, `perf-k6`, `perf-locust`, `mobile` and `fullstack`.
- `testkeel list-types`, `testkeel version` and `testkeel assignment check`.
- `testkeel report`: a self-contained HTML summary of one or more result files.
- Result model `testkeel.model.v1` and stable test identity in `testkeel.model.identity`.
- JUnit XML and Playwright JSON importers behind `testkeel.importers.registry`.
- Plugin interface `testkeel.plugin_api.v1` with the entry-point groups
  `testkeel.importers`, `testkeel.result_types` and `testkeel.reporters`.
- The leak gate, `python -m testkeel.leakgate`.

[Unreleased]: https://github.com/RAJUSHANIGARAPU/aurseal/compare/testkeel-v0.1.0...HEAD
[0.1.0]: https://github.com/RAJUSHANIGARAPU/aurseal/releases/tag/testkeel-v0.1.0
