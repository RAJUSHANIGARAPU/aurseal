# Quickstart

## Install

The package is not on PyPI yet; install it from the repository. Either tool keeps it in
its own environment and puts `testkeel` on your `PATH`:

```bash
uv tool install git+https://github.com/RAJUSHANIGARAPU/wiki_project
# or
pipx install git+https://github.com/RAJUSHANIGARAPU/wiki_project
```

Check the install:

```bash
testkeel version
testkeel list-types
```

Generated projects use [uv](https://docs.astral.sh/uv/) and `make`; the `web-ui` type
also downloads a Chromium build on first run.

## Generate a project

```bash
testkeel new-assignment --type web-ui --name acme-checkout
```

This renders the project into a staging directory, runs the leak gate over it, and only
then moves it to `./acme-checkout`. With `--git` (the default) it also runs `git init`
without committing, so the first commit is yours. Add `--non-interactive` in scripts:
without it, missing answers are asked for when a terminal is attached.

Run it:

```bash
cd acme-checkout
make check   # lint, strict type check and the offline tests
make test    # the whole suite against the target, in parallel
```

`make help` lists every target. The project's `README.md` opens with a decisions table
to confirm or rewrite before you hand the work in.

## Other types

```bash
testkeel new-assignment --type api-rest --name acme-orders-api --api-base-url https://api.example.test
testkeel new-assignment --type strategy --name acme-test-strategy
```

`--dry-run` lists what would be written without writing anything. See the
[CLI reference](cli.md) for every option.

## Check a project before you submit it

```bash
testkeel assignment check acme-checkout
```

Exit code 0 means clean; 4 means findings, printed as a location and a rule id. See
[Leak gate](leak-gate.md).
