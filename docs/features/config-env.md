# Configuration, environments and run tagging

Modules: `core/config_reader.py`, `core/env_file.py`, `core/run_id.py`, `core/env_profile.py`,
`core/config_consistency.py`, `config/env/.env.example`.

## Configuration ladder

### What it does

`ConfigReader` answers every configuration key from one precedence ladder. The
first non-blank answer wins:

1. `os.environ`, under the key's environment-variable spelling
   (`base_url` -> `BASE_URL`, `playwright.timeout.ms` -> `PLAYWRIGHT_TIMEOUT_MS`);
2. the git-ignored `.env.<env>` file at the repository root, under the same spelling;
3. the committed `config/environments.json` entry for the active environment;
4. the default the caller passes.

A blank value counts as absent at every rung. CI systems export a parameter
nobody filled in as an empty string, and that must not hide a real value lower
down the ladder.

An unknown environment fails as soon as the reader is constructed, with a
message listing the environments that exist. Without that check it would show
up later as a `KeyError` on `base_url`.

### How to use it

```python
from core.config_reader import ConfigReader

config = ConfigReader("qa")  # or ConfigReader(None): TEST_ENV, then qa
base_url = config.get_base_url()
timeout = config.get_int("playwright.timeout.ms", 30000)
headless = config.get_bool("headless", True)
where = config.source_of("base_url")  # "env", "env_file", "file" or None
```

`config.get(key)` without a default raises `KeyError` naming the environment and
the variable to set. `get_bool`, `get_int` and `get_float` fall back to the
default, and log a warning, when the value cannot be parsed.

For tests, inject the sources instead of touching the real environment:

```python
from core.config_reader import ConfigReader, ConfigSources

config = ConfigReader(
    "qa",
    sources=ConfigSources(
        os_env={"BASE_URL": "https://override.example.test"},
        env_file={},
        file_config={"base_url": "https://qa.example.test"},
    ),
)
```

### Configuration

| Setting | Default | Meaning |
|---|---|---|
| `--env` | none (see below) | explicit environment |
| `TEST_ENV` | `qa` | environment when `--env` is not given |
| `<KEY>` | — | overrides `<key>` from any file |
| `.env.<env>` | absent | per-environment local secrets (already ignored by `.env.*`) |

## The `.env.<env>` file

### What it does

`core/env_file.py` reads `.env.qa`, `.env.staging` and so on from the repository
root. It is one rung of the ladder, never an override: a variable exported in the
shell or by CI always wins. It supports `KEY=value`, an `export` prefix, `#`
comments, and single- or double-quoted values. Variables are not expanded. A
malformed line is logged by line number only, never with its content.

### Example

```
# .env.qa  (never committed)
AUTH_USERNAME=tester@example.test
AUTH_PASSWORD='s3cret#with-hash'
```

## `TEST_ENV` and the `--env` default

`resolve_env` takes the explicit `--env` value first, then `TEST_ENV`, then `qa`.
While the root `conftest.py` declares `--env` with `default="qa"`, the option
always has a value and `TEST_ENV` never takes effect. Set the default to `None`:

```python
def pytest_addoption(parser):
    parser.addoption(
        "--env", action="store", default=None, help="qa | staging (default: $TEST_ENV or qa)"
    )
```

`ConfigReader(None)` resolves the same way, so the `config` fixture does not change.

## Run id and test-traffic tagging

### What it does

Every request the suite sends can be recognised as test traffic, and every
request in one run carries the same id:

- **API clients** send `User-Agent: <product>/<run id> python-requests` and an
  `X-Test-Run-Id: <run id>` header (`run_id.api_headers()`).
- **The browser** keeps its own User-Agent and only has the token *appended*
  (`run_id.browser_user_agent(default)`). It gets no extra header, because a page
  calling an API on another origin would then send a CORS preflight for every
  call, and a firewall that keys on the browser's User-Agent must still see what
  it saw before.

The id is resolved in this order:

1. `TEST_RUN_ID`;
2. a CI build id: Jenkins `BUILD_TAG`, or GitHub Actions `gh-<GITHUB_RUN_ID>-<GITHUB_RUN_ATTEMPT>`;
3. `local-<short host>-<yyyymmdd-HHMMSS>`.

The result is sanitised to `[A-Za-z0-9._-]` and capped at 64 characters.

**One id per run under xdist.** Each worker is its own process, so each would
mint its own local id. `pin_for_session()` resolves the id on the controller and
exports it as `TEST_RUN_ID` before the workers start, and the workers inherit
it. The reporting plugin (`reporting/pytest_plugin.py`) calls it for you.
Against the real environment, the first id minted in a process is also exported,
so it stays stable between calls.

### How to use it

```python
import requests
from core import run_id

session = requests.Session()
session.headers.update(run_id.api_headers())
```

In the root `conftest.py`:

```python
@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    from core import run_id

    args = {**browser_context_args, "viewport": {"width": 1280, "height": 800}}
    tagged = run_id.browser_user_agent(args.get("user_agent"))
    if tagged:
        args["user_agent"] = tagged
    return args
```

If you have no User-Agent of your own, `run_id.tagged_user_agent(browser)` reads
the browser's real one from a throwaway page and appends the token.
`ui/fixtures/session_fixtures.py:tagged_context_args` does this for you.

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `TEST_RUN_ID` | resolved as above | explicit run id |
| `TEST_RUN_TAGGING` | `true` | `false` turns all tagging off |
| `TEST_RUN_PRODUCT` | `test-automation` | product token before the `/` |

### Example

```
TEST_RUN_ID=nightly-42 pytest -n auto
# API:     User-Agent: test-automation/nightly-42 python-requests
#          X-Test-Run-Id: nightly-42
# Browser: Mozilla/5.0 ... Chrome/140.0 test-automation/nightly-42
```

## Per-environment profile

### What it does

Every environment in `config/environments.json` defines the same keys, not just
`base_url`: the API base URL, browser defaults, timeouts, artifact toggles, the AI
model and its timeout, a read-only switch and an API-assertion switch. A key that
one environment has and another lacks becomes a default the other one silently
inherits from code, so the key set is a contract (`core.env_profile.REQUIRED_KEYS`)
and an offline test holds every committed environment to it.

There is also a `local` environment that points at a system under test running
on your own machine (`http://localhost:8800/`).

`EnvProfile.from_config(config)` reads the profile through the normal ladder, so a
shell variable or a `.env.<env>` line overrides any of it. It is strict: a missing
key raises `KeyError` naming the variable, and `headless=ture`, `timeout_ms=30s`,
an unknown browser or an unknown artifact mode raise `ValueError` instead of
falling back to a default.

### How to use it

```python
from core.config_reader import ConfigReader
from core.env_profile import EnvProfile

profile = EnvProfile.from_config(ConfigReader("local"))
browser = playwright[profile.browser].launch(**profile.playwright_launch_args())
page.set_default_timeout(profile.timeout_ms)
```

### Configuration

| Key (variable) | `local` | `qa` | `staging` | Meaning |
|---|---|---|---|---|
| `base_url` (`BASE_URL`) | `http://localhost:8800/` | site | site | UI entry point |
| `api_base_url` (`API_BASE_URL`) | `http://localhost:8800/api/` | site | site | API root |
| `browser` (`BROWSER`) | `chromium` | `chromium` | `chromium` | `chromium`, `firefox` or `webkit` |
| `headless` (`HEADLESS`) | `true` | `true` | `true` | headless browser |
| `timeout_ms` (`TIMEOUT_MS`) | `15000` | `30000` | `45000` | default action timeout |
| `slow_mo_ms` (`SLOW_MO_MS`) | `0` | `0` | `0` | delay between actions |
| `video`, `tracing` | `retain-on-failure` | same | same | `on`, `off` or `retain-on-failure` |
| `ai_model` (`AI_MODEL`) | `claude-sonnet-4-6` | same | same | model for the AI helpers |
| `ai_timeout_s` (`AI_TIMEOUT_S`) | `30` | `30` | `45` | AI call timeout |
| `auth_mode` (`AUTH_MODE`) | `none` | `none` | `none` | see the auth page |
| `read_only` (`READ_ONLY`) | `false` | `false` | `true` | staging is a read-only smoke target |
| `api_assertions_enabled` | `true` | `true` | `true` | run API-side assertions |
| `business_timezone` (`BUSINESS_TIMEZONE`) | `UTC` | `UTC` | `UTC` | zone the service computes its calendar days in; `core.clock.today()` and `core.clock.local_date()` use it (optional, `UTC` when unset) |

### Example

```
TEST_ENV=local TIMEOUT_MS=5000 pytest tests/ -k smoke
```

## Host-family consistency check

### What it does

`core/config_consistency.py` checks that every endpoint an environment names
belongs to that environment. For each environment, every `*_url` and `*_host`
key must share the host family of `base_url`:

- the registrable domain (last two labels, or three under `co.uk`-style suffixes);
- `localhost` for every loopback name or address (`localhost`, `127.0.0.1`, `::1`,
  `*.localhost`);
- or an explicit alias, for an environment that legitimately spans two domains.

Every endpoint key that *any* environment defines must be defined by *all* of
them. A missing or blank key is reported; it must never fall back to another
environment's host, which is how a run ends up driving one environment's UI
against another environment's backend.

The offline test runs the check over the real `config/environments.json`.

### How to use it

```python
import json
from core.config_consistency import check_environments, same_host_family

problems = check_environments(
    json.load(open("config/environments.json")),
    aliases={"example-cdn.test": "example.test"},  # optional
    required=["api_base_url"],  # keys every env must have
)
assert problems == [], "\n".join(problems)

same_host_family("https://a.example.test", "https://api.example.test")  # True
```

### Configuration

None; `aliases` and `required` are arguments.

### Example output

```
staging: docs_host is missing (it must not fall back to another env)
qa: api_url host 'api.other.test' is not in the family of base_url host 'qa.example.test'
```

## Local `.env` template

### What it does

`config/env/.env.example` is the committed template for a developer's local
`.env.<env>` file. It lists every key the auth ladder reads (`AUTH_MODE`,
`LOGIN_URL`, `POST_LOGIN_URL`, `AUTH_USERNAME`, `AUTH_PASSWORD`,
`AUTH_ORGANIZATION`, `AUTH_TOKEN_URL`, `AUTH_CLIENT_ID`, `AUTH_CLIENT_SECRET`,
`AUTH_AUDIENCE`, `AUTH_TOKEN_COOKIE_NAME`, `AUTH_HEADLESS`,
`AUTH_SESSION_TTL_SECONDS`, `SUPPLIED_TOKEN`) and every value is the placeholder
`REPLACE_ME`.

An offline test keeps it honest: it records which keys `AuthSettings.from_config`
actually reads and fails when the template misses one or lists one nothing reads,
when any value is not a placeholder, or when `.gitignore` stops ignoring `.env.*`
(or starts ignoring the template).

### How to use it

```
cp config/env/.env.example .env.qa     # at the repository root
# edit .env.qa: replace what you need, delete the rest
pytest --env=qa
```

### Configuration

The file itself is the configuration; see "The `.env.<env>` file" above for how
it is read.
