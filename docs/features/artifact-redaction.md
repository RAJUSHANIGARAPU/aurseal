# Artifact redaction

Trace zips, run logs and report attachments are archived on CI, and anyone who can
download them can read what the browser and the framework saw. That includes the test
user's live session: cookie values, `Authorization` headers, CSRF tokens, bearer
tokens, OAuth `code` / `state` parameters and pre-signed URL signatures. A live
session is replayable until it expires. Personal data (emails, IBANs) in a log is a
privacy problem of its own.

The code is `core/artifact_redaction.py`. It is a sibling of
`contract_testing/redaction.py`, not an extension: that module decides by *name* for
contract documents, this one works on free text and event streams where the name is
often missing, so it decides by *value shape* or by *known value*. Both emit the same
`<redacted>` marker.

## What is masked

| Function | Masks |
|---|---|
| `credentials(text)` | JWTs, `Bearer` / `Basic` values, `cookie` / `set-cookie` / `authorization` / `x-xsrf-token` / `x-csrf-token` header lines, URL parameters `code`, `state`, `access_token`, `refresh_token`, `id_token`, `token`, `client_secret`, `api_key`, `signature`, `X-Amz-Signature`, `X-Amz-Security-Token`, `X-Amz-Credential` |
| `redact(text)` | everything above, plus emails (`***@domain` keeps the domain) and IBANs with a valid check (country and last four kept) |

The parameter name stays and the value goes, so a pre-signed URL keeps its path and
`X-Amz-Expires`. Identifiers (UUIDs, hashes, version strings, `error_code=`) are left
alone.

Every pattern is linear-time. A run of characters is scanned once, repeats are
bounded, and there is no alternation over an unbounded string. A test feeds a 20 KB
cookie value through each pattern.

## Traces

`redact_trace(path, known_values)` rewrites a Playwright trace zip in place:

- `*.trace` and `*.network` events are parsed line by line. HAR-style
  `{"name": "Cookie", "value": ...}` pairs (and the same for `set-cookie`,
  `authorization`, `x-xsrf-token`, `x-csrf-token`, and query-string parameters named
  above), and every `"cookies": [...]` list, lose their values. Every string in an
  event then loses known values and tokens, which also covers plain log lines such as
  `"message":"  cookie: ..."`.
- `.json`, `.html` and `.txt` resources lose known values and tokens.
- Everything else (screenshots, other binaries) is copied unchanged.

Known values are the browser context's cookie values, read **before** the trace is
written (`cookie_values(context)`). Each is matched raw, JSON-escaped and
percent-encoded. Values under 6 characters are ignored.

**Fail closed.** Any failure while rewriting (corrupt zip, undecodable event file, disk
error) deletes the trace and the scratch file. An unredacted trace is never left on
disk. The warning names the file and the exception type, never content.

## Where it runs

| Place | What |
|---|---|
| `core/artifact_policy.finish_trace` (used by the root `conftest.py`) | reads cookies, stops the trace, redacts; returns `None` when the trace was deleted |
| `qaplatform/capture.py` | same for exploratory-session traces |
| root `conftest.py` | `RedactingFilter` on the `reports/logs/test.log` handler |

## Limits

- Screenshots, video and the trace's embedded images are not rewritten. A token shown
  on screen stays in them.
- Only values the context holds as cookies are known values. A bearer token kept in
  `localStorage` is caught by its shape (JWT, `Bearer`, `access_token=`) but an opaque
  one is not; pass it as `known_values` if you have it.
- Cookies that rotated and were already gone before the trace stopped are caught only
  by header shape.
- `redact` does not mask names, addresses or other free-form personal data.
- Trace events keep personal data; only tokens and session values are removed so the
  trace stays useful for debugging.
- Redaction is a second line of defence. Keep artifacts access-controlled and
  short-lived.
