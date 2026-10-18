# Releasing

How a `aurseal` version is numbered, what it promises, and how it reaches PyPI.

## What is public contract

These are the only parts of the package covered by the compatibility promise. Everything
else (module layout, helper functions, template file contents, the HTML report's markup)
may change in any release.

| Surface | Where | Promise |
|---|---|---|
| Result model | `aurseal.model.v1` (`RunUpload`, `TestRecord`, `Status`, ...) and `schema_version` inside every document | A field removal or a change of meaning is a new module (`v2`) and a major release, never an edit to `v1` |
| Test identity | `aurseal.model.identity` (`explicit_id`, `fingerprint`) | A change to a fingerprint is a major release: it would re-identify, and so duplicate, every stored test |
| Importer registry | `aurseal.importers.registry` (`importers`, `sniff`, `detect`, `parse`, `parse_file`) | New importers arrive in minor releases; a fix to a parser's output is a patch release with a changelog entry |
| Plugin interface | `aurseal.plugin_api.v1` and the entry-point groups `aurseal.importers`, `aurseal.result_types`, `aurseal.reporters` | One major version supported and the previous one deprecated, never removed without a major release |
| CLI | Command names, option names and exit codes in the [CLI reference](cli.md) | Removing or renaming any of them, or changing an exit code's meaning, is a major release; new commands and options are minor |

## Version numbers

`aurseal` follows [Semantic Versioning 2.0.0](https://semver.org/) for the surfaces
above:

- **MAJOR** for an incompatible change to any of them.
- **MINOR** for a backward-compatible addition: a new importer, command, option, model
  field with a default, plugin group or template type.
- **PATCH** for a backward-compatible fix.

While the version is `0.y.z`, SemVer allows anything to change at any time; pin an exact
version until 1.0. Every breaking change is still listed in the changelog.

## Deprecation

A surface is removed only after it has been deprecated for **at least two minor releases
and at least 90 days, whichever is longer**, and only in a major release. While
deprecated it keeps working, emits a `DeprecationWarning` naming its replacement, and is
listed under *Deprecated* in the changelog of the release that deprecated it.

## The changelog

[`CHANGELOG.md`](../../CHANGELOG.md) at the repository root follows
[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/). Every pull request that
changes behaviour adds a line under *Unreleased* (*Added*, *Changed*, *Deprecated*,
*Removed*, *Fixed* or *Security*). A release renames *Unreleased* to the new version and
date.

## Cutting a release

The version is written in one place, `src/aurseal/__init__.py`; `pyproject.toml` reads it
from there and `aurseal --version` prints it.

1. On a branch, set `__version__` in `src/aurseal/__init__.py` and move the changelog's
   *Unreleased* entries under the new version. Merge the pull request.
2. Tag the merge commit on `main` and push the tag:

    ```bash
    git tag -a aurseal-v0.2.0 -m "aurseal 0.2.0"
    git push origin aurseal-v0.2.0
    ```

3. The push starts `.github/workflows/publish.yml`. Its build job fails, and nothing is
   published, when:
    - the tagged commit is not on the default branch;
    - the tag is not exactly `aurseal-v` + `__version__`;
    - `twine check --strict` rejects the sdist or wheel metadata;
    - the wheel holds anything outside `aurseal/` (`python -m tools.wheel_check`);
    - the wheel, installed alone in a clean environment, cannot run
      `aurseal --version` and `aurseal list-types`.
4. The `pypi` job then publishes the build through the `pypi` environment with trusted
   publishing: GitHub issues a short-lived token to that job only, and no PyPI token is
   stored in the repository.

Release tags are always `aurseal-v<version>`. Plain `v*` tags belong to the release
verdict workflow (`release.yml`) and never publish the package;
`tests/package/test_publish_workflow.py` checks both directions against every workflow
in the repository. A workflow runs from the tagged commit, so a tag on a commit that
predates `publish.yml` publishes nothing.

### Rehearsing on TestPyPI

Run the *Publish* workflow by hand (Actions → Publish → Run workflow). The same build job
runs, skipping the two tag checks, and the `testpypi` job uploads to
[TestPyPI](https://test.pypi.org/project/aurseal/). A manual run never publishes to PyPI.
TestPyPI refuses a version it already has, so a second rehearsal needs a new version,
for example a `.devN` suffix on the branch.

## One-time setup on PyPI and GitHub

Done once by the project owner before the first publish. The workflow cannot do any of
it.

1. On pypi.org, at <https://pypi.org/manage/account/publishing/>, add a pending publisher
   (GitHub tab):

    | Field | Value |
    |---|---|
    | PyPI project name | `aurseal` |
    | Owner | `RAJUSHANIGARAPU` |
    | Repository name | `aurseal` |
    | Workflow name | `publish.yml` |
    | Environment name | `pypi` |

    A pending publisher does not reserve the name: whoever uploads `aurseal` first owns
    it. The first successful run of the `pypi` job creates the project.

2. On test.pypi.org (a separate account), at
   <https://test.pypi.org/manage/account/publishing/>, add the same pending publisher with
   environment name `testpypi`.
3. In the GitHub repository, *Settings → Environments*, create `pypi` and `testpypi`.
   On `pypi`, add yourself as a required reviewer and restrict deployment to tags
   matching `aurseal-v*`, so a publish waits for an explicit approval.
4. If the repository is renamed, update the trusted publisher on PyPI and TestPyPI to
   the new repository name before the next release, or the upload is refused.
