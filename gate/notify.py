"""One chat message when a default-branch or scheduled run is red, or green but not evidence.

* :func:`describe` - the one-line run description: run-trust verdicts (or an explicit "no
  trust verdict was written"), target environment, external-config commit (8 characters,
  or "not bound"), with an optional nightly prefix. The publish job writes it to the step
  summary and exports it as a job output.
* :func:`decide` - ``failed`` when a needed job failed; ``not evidence`` when nothing
  failed but a lane's verdict is FAIL under ``unstable`` enforcement or a job was
  cancelled; otherwise nothing. Only the default branch and scheduled runs notify, unless
  the repository variable ``NOTIFY_ALL_BRANCHES`` is true (pull requests get the PR
  feedback comment instead).
* :func:`send` - one JSON POST ``{"text": ...}`` to the incoming webhook in the
  ``NOTIFY_WEBHOOK_URL`` secret, 10 s timeout, one retry on a 5xx or a timeout. A failure
  to deliver is a ``::warning::``, never a red run; no webhook configured is a log line.
  The URL never appears in output: it is redacted from every message, exceptions included.

``python -m gate.notify describe|send``; both exit 0 unless their arguments are wrong.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path

from gate._env import env_bool
from gate.integrity import _FIRST_LINE_RE
from gate.schedules import trigger_from_env

WEBHOOK_ENV = "NOTIFY_WEBHOOK_URL"
ALL_BRANCHES_ENV = "NOTIFY_ALL_BRANCHES"
SEPARATOR = " | "
DEFAULT_MAX_CHARS = 500
DEFAULT_TIMEOUT_S = 10.0
FAILED = "failed"
NOT_EVIDENCE = "not evidence"
NO_VERDICT = "no trust verdict was written"


def verdict_lines(reports: str | Path) -> list[str]:
    """First line of every run-trust text marker under ``reports`` (any depth)."""
    lines = []
    base = Path(reports)
    if not base.is_dir():
        return lines
    for marker in sorted(base.rglob("run-trust*.txt")):
        try:
            first = marker.read_text(encoding="utf-8").splitlines()[:1]
        except OSError:
            continue
        if first and _FIRST_LINE_RE.match(first[0]):
            lines.append(first[0])
    return lines


def parse_verdicts(lines: Iterable[str]) -> list[tuple[str, str]]:
    found = []
    for line in lines:
        match = _FIRST_LINE_RE.match(line)
        if match:
            found.append((match.group(1).upper(), match.group(2).lower()))
    return found


def describe(
    verdicts: Sequence[str],
    environment: str,
    config_commit: str | None,
    prefix: str | None = None,
) -> str:
    parts = [prefix] if prefix else []
    parts.append("; ".join(verdicts) if verdicts else NO_VERDICT)
    parts.append(f"env {environment or 'unknown'}")
    parts.append(f"config {config_commit[:8]}" if config_commit else "config not bound")
    return SEPARATOR.join(parts)


def decide(
    results: Mapping[str, str],
    verdicts: Iterable[tuple[str, str]],
    *,
    event: str,
    ref: str,
    default_branch: str = "main",
    notify_all: bool = False,
) -> str | None:
    on_default = ref in (default_branch, f"refs/heads/{default_branch}")
    if not (event == "schedule" or on_default or notify_all):
        return None
    states = set(results.values())
    if "failure" in states:
        return FAILED
    unstable_fail = any(v == "FAIL" and e == "unstable" for v, e in verdicts)
    if unstable_fail or "cancelled" in states:
        return NOT_EVIDENCE
    return None


def truncate(text: str, limit: int = DEFAULT_MAX_CHARS) -> str:
    return text if len(text) <= limit else text[: max(limit - 1, 0)] + "…"


def message(
    status: str,
    description: str,
    environ: Mapping[str, str],
    limit: int = DEFAULT_MAX_CHARS,
) -> str:
    repo = environ.get("GITHUB_REPOSITORY", "")
    branch = environ.get("GITHUB_REF_NAME", "")
    run_url = (
        f"{environ.get('GITHUB_SERVER_URL', '')}/{repo}/actions/runs/"
        f"{environ.get('GITHUB_RUN_ID', '')}"
    )
    return f"[{status}] {repo} {branch}: {truncate(description, limit)} {run_url}"


def redact(text: str, secret: str) -> str:
    return text.replace(secret, "<webhook>") if secret else text


Opener = Callable[..., object]


def send(
    url: str | None,
    text: str,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    opener: Opener | None = None,
    out=None,
) -> bool:
    """POST once, retry once on a 5xx or timeout. Never raises; never prints the URL."""
    out = out or sys.stdout
    if not url:
        print("notification not configured", file=out)
        return False
    opener = opener or urllib.request.urlopen
    body = json.dumps({"text": text}).encode("utf-8")
    last = ""
    for _attempt in range(2):
        request = urllib.request.Request(
            url, data=body, method="POST", headers={"Content-Type": "application/json"}
        )
        try:
            response = opener(request, timeout=timeout_s)
            status = int(getattr(response, "status", None) or getattr(response, "code", 0))
            if 200 <= status < 300:
                print("notification sent", file=out)
                return True
            last = f"HTTP {status}"
            if status < 500:
                break
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
            if exc.code < 500:
                break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = redact(str(getattr(exc, "reason", exc)), url)
    print(f"::warning title=Notification::not delivered ({redact(last, url)})", file=out)
    return False


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
    opener: Opener | None = None,
) -> int:
    environ = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(prog="python -m gate.notify")
    sub = parser.add_subparsers(dest="command", required=True)
    desc = sub.add_parser("describe", help="print and export the one-line run description")
    send_p = sub.add_parser("send", help="notify when the run failed or is not evidence")
    for p in (desc, send_p):
        p.add_argument("--reports", default="reports")
        p.add_argument("--env", default=environ.get("TEST_ENV", "qa"))
        p.add_argument("--config-commit", default=environ.get("EXT_CONFIG_COMMIT", ""))
        p.add_argument("--prefix", default="")
    desc.add_argument("--summary", default=environ.get("GITHUB_STEP_SUMMARY"))
    desc.add_argument("--github-output", default=environ.get("GITHUB_OUTPUT"))
    send_p.add_argument("--needs-json", default="{}")
    send_p.add_argument("--description", default="")
    send_p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    send_p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    args = parser.parse_args(argv)

    lines = verdict_lines(args.reports)
    description = args.__dict__.get("description") or describe(
        lines, args.env, args.config_commit, args.prefix or None
    )
    if args.command == "describe":
        print(description)
        if args.summary:
            with open(args.summary, "a", encoding="utf-8") as handle:
                handle.write(f"Run: {description}\n")
        if args.github_output:
            with open(args.github_output, "a", encoding="utf-8") as handle:
                handle.write(f"description={description}\n")
        return 0

    needs = json.loads(args.needs_json or "{}")
    results = {name: str(info.get("result", "")) for name, info in needs.items()}
    status = decide(
        results,
        parse_verdicts(lines),
        event=trigger_from_env(environ),
        ref=environ.get("GITHUB_REF", ""),
        default_branch=environ.get("DEFAULT_BRANCH", "main"),
        notify_all=env_bool(ALL_BRANCHES_ENV, False, environ),
    )
    if status is None:
        print("no notification: the run is green and evidence, or not a notifying branch")
        return 0
    text = message(status, description, environ, args.max_chars)
    send(environ.get(WEBHOOK_ENV, "").strip() or None, text, timeout_s=args.timeout, opener=opener)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
