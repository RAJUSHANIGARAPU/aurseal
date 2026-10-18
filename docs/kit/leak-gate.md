# Leak gate

The leak gate keeps two kinds of text out of anything you publish: names you have listed
as private (an employer, a client, an internal project) and personal data. It runs in
three places:

| Where | Command | What it scans |
|---|---|---|
| Every generated project, before it is written | `aurseal new-assignment` | the rendered tree, the project name and its slug; with the [project rules](#project-rules) |
| A project you have edited since | `aurseal assignment check [PATH]` | the project tree; with the project rules |
| This repository's CI | `python -m aurseal.leakgate scan` | the tree, pull request title and body, commit messages and added lines |

## What it checks

**Your denylist** (rules `deny.term`, `deny.qual`, `deny.re`). Text is normalised first
(Unicode NFKC, invisible format characters removed, look-alike characters folded) and
split into tokens at punctuation, case changes and letter/digit boundaries, so one entry
catches the usual spellings of a name.

**Built-in personal-data rules**, which need no list and so run everywhere:

| Rule | Finds | Ignores |
|---|---|---|
| `pii.email` | an email address | reserved test domains (`example.com`, `*.test`, `*.example`, `*.invalid`, `*.localhost`) and public no-reply addresses |
| `pii.iban` | an IBAN whose length fits its country and whose mod-97 check passes | strings that only look like one |
| `pii.phone` | an international (`+`) number of 8 to 15 digits, or a 10-digit Dutch national number | versions, hashes, timestamps, ids, lock files |
| `pii.home` | a home directory path that names a user, or the scanning machine's own home directory | placeholder user names |

### Project rules

A generated project is held to more than that. These rules run in `new-assignment` and
`assignment check`, never in the repository scan (`python -m aurseal.leakgate scan`),
because a repository may legitimately link anywhere, keep editor config or import
`aurseal`:

| Rule | Finds |
|---|---|
| `host.unlisted` | a URL, or a container image registry, whose host is not the declared `--target-url` / `--api-base-url` (or a subdomain of it), a reserved or private name (`localhost`, `*.test`, `example.com`, private IP addresses, single-label service names), a public demo target (`saucedemo.com`, `restful-booker.herokuapp.com`, `reqres.in`, ...) or a standard tooling host (`pypi.org`, `github.com`, `mcr.microsoft.com`, `ghcr.io`, `playwright.dev`, ...) |
| `trace.tool` | assistant or editor config: `.claude/`, `.cursor*`, `CLAUDE.md`, `AGENTS.md`; and `.copier-answers.yml` unless the project was made with `--updatable` (or the file names this repository as its source) |
| `lock.index` | a URL in `uv.lock` that is not `pypi.org` or `files.pythonhosted.org` |
| `standalone.import` | a Python file that imports `aurseal` |
| `standalone.dependency` | `aurseal` as a dependency in `pyproject.toml`, a `requirements*.txt` file or `uv.lock` |

A host you need beyond the target goes on its own line in
`~/.config/aurseal/allow-hosts.txt` (or `--allow-host` for `assignment check`). The
task brief in `.task/` is git-ignored, so the project rules skip it.

### Your git identity

`new-assignment` (with `--git`, the default) checks the identity git will put on your
first commit (`user.name`, `user.email`, from your global config or the environment)
against the denylist and warns when it matches. It never changes your git config and
never commits.

## What it prints

A finding is a location and a rule id, for example `README.md:12 deny.term [e3]`
(the entry on line 3 of the list).
It never prints the matched text, and an error in the list names the entry by its line
number. A log or CI summary therefore never republishes what the gate found.

## Configuring the denylist

The list never lives in a repository. The first source found wins:

1. `--denylist <file>` (module entry point only);
2. the environment variable `LEAK_DENYLIST`, holding the whole list (in CI: a secret);
3. `~/.config/aurseal/denylist.txt`.

The same file under the package's previous name (see the changelog) is still read, with a
`DeprecationWarning`, when `~/.config/aurseal/denylist.txt` is absent; the same holds for
`allow-hosts.txt`. Move both files; the fallback goes after the next release.

One entry per line; a line starting with `#` is a comment:

```text
# synthetic examples only
term:acme corp
qual:acme_server.billing
re:BUG-\d{4,}
```

| Prefix | Matches |
|---|---|
| `term:` | 1 to 3 tokens, in any spelling: `term:acme corp` hits `AcmeCorp`, `acme_corp` and `acme-corp.example`, but not `acmecorpus`. A single common word is rejected when the list loads; use `qual:` or a more distinctive name. |
| `qual:` | an exact sequence of two or more tokens, for module names that are common words on their own. |
| `re:` | a regular expression, searched in each normalised line. |

A line without a prefix is a `term:`.

To use it in GitHub Actions, store the list as a repository secret and pass it through
the environment, never through an expression inside a script:

```yaml
- name: Leak check
  env:
    LEAK_DENYLIST: ${{ secrets.LEAK_DENYLIST }}
  run: python -m aurseal.leakgate scan --tree .
```

## When no denylist is available

- `new-assignment` and `assignment check` warn and still run the personal-data rules.
  `assignment check --strict` fails instead (exit 2).
- `python -m aurseal.leakgate scan` fails (exit 2) unless `--missing-list warn` is
  given. With `--fork` (a pull request from a fork, which cannot read secrets) it reports
  the denylist as not assessed (exit 3) when the built-in rules find nothing.

Exit codes of the module entry point: 0 clean, 1 findings, 2 cannot run, 3 not assessed.
`new-assignment` reports a failed gate as exit 4 and writes nothing; `assignment check`
reports findings as exit 4.

## False positives

- A personal-data false positive goes in `leakgate-allow.toml` at the root of the scanned
  tree, with a rule, a path glob and a reason:

  ```toml
  [[allow]]
  rule = "pii.phone"
  path = "tests/fixtures/**"
  reason = "invented numbers in the form validation table"
  ```

- A denylist false positive is fixed in the list itself. Denylist rules cannot be
  allowlisted in a committed file, because the entry would have to describe the hit.
