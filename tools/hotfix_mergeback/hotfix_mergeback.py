"""Which fixes run in production that the next release from mainline would drop.

Given the version manifest of production (service -> deployed git ref, the format
``promotion_diff`` reads) and local clones of the services, the report lists for
every service the commits reachable from the production ref but not from the
mainline branch. A production-only commit is cleared, as in ``promotion_diff``,
when mainline already carries it as the same patch (patch-id), under the same
subject, or with identical content in every file it touched. What is left is a
fix the next release cut from mainline would silently take out of production.

Ticket keys are pulled from the subjects with a configurable regex; housekeeping
commits matching the noise file are listed apart and do not fail the run.

Exit status: 0 report written; 1 bad input; 2 with ``--fail-on-missing`` when a
non-noise commit is missing from mainline, or a service could not be checked (an
unmeasured service is not a clean one).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from tools.promotion_diff.promotion_diff import (
    DEFAULT_KEY_PATTERN,
    DEFAULT_NOISE_FILE,
    Git,
    InputError,
    Inverted,
    Pin,
    _md,
    check_inversion,
    is_noise,
    keys_in,
    load_noise,
    load_pins,
)


@dataclass
class ServiceReport:
    service: str
    ref: str
    mainline: str
    problem: str | None = None
    only_in_production: list[Inverted] = field(default_factory=list)

    @property
    def missing(self) -> list[Inverted]:
        return [i for i in self.only_in_production if i.equivalence is None]

    @property
    def cleared(self) -> list[Inverted]:
        return [i for i in self.only_in_production if i.equivalence]


def resolve_mainline(git: Git, mainline: str) -> str | None:
    """The remote-tracking branch first: a stale local branch would misreport."""
    done = git.run(
        "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{mainline}^{{commit}}",
        check=False,
    )  # fmt: skip
    if done.returncode == 0:
        return done.stdout.strip()
    return git.resolve(mainline)


def analyse_service(workspace: Path, pin: Pin, mainline: str, fetch: bool) -> ServiceReport:
    report = ServiceReport(pin.service, pin.ref, mainline)
    repo = workspace / (pin.repo or pin.service)
    git = Git(repo)
    if not git.exists():
        report.problem = f"no git clone at {repo}"
        return report
    if fetch:
        warning = git.fetch()
        if warning:
            print(f"warning: fetch failed for {pin.service}: {warning}", file=sys.stderr)
    prod_sha = git.resolve(pin.ref)
    if prod_sha is None:
        report.problem = f"production ref {pin.ref!r} not found in {repo}"
        return report
    main_sha = resolve_mainline(git, mainline)
    if main_sha is None:
        report.problem = f"mainline {mainline!r} not found in {repo}"
        return report
    report.only_in_production = check_inversion(git, main_sha, prod_sha)
    return report


def split_missing(
    missing: Sequence[Inverted], noise: Sequence[re.Pattern[str]], key_re: re.Pattern[str]
) -> tuple[list[Inverted], list[Inverted]]:
    """``(fixes, noise)``. A commit naming a key is never noise."""
    fixes, noisy = [], []
    for inv in missing:
        subject = inv.commit.subject
        if not keys_in(subject, key_re) and is_noise(subject, noise):
            noisy.append(inv)
        else:
            fixes.append(inv)
    return fixes, noisy


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------


def headline(
    reports: Sequence[ServiceReport], noise: Sequence[re.Pattern[str]], key_re: re.Pattern[str]
) -> dict[str, int]:
    fixes = noisy = affected = 0
    for r in reports:
        f, n = split_missing(r.missing, noise, key_re)
        fixes, noisy = fixes + len(f), noisy + len(n)
        affected += bool(f)
    return {
        "missing": fixes,
        "noise": noisy,
        "services_affected": affected,
        "services_not_checked": sum(1 for r in reports if r.problem),
    }


def render(
    reports: Sequence[ServiceReport],
    label: str,
    mainline: str,
    noise: Sequence[re.Pattern[str]],
    key_re: re.Pattern[str],
) -> str:
    head = headline(reports, noise, key_re)
    lines = [f"# Hotfix merge-back: {label} -> {mainline}", ""]
    summary = (
        f"**{head['missing']} commit(s) running in {label} are not on {mainline}**"
        f" across {head['services_affected']} service(s)"
    )
    if head["noise"]:
        summary += f"; {head['noise']} more match the noise patterns"
    if head["services_not_checked"]:
        summary += f"; **{head['services_not_checked']} service(s) not checked**"
    lines += [summary + ".", ""]
    header = ["Service", label, "Missing", "Keys", "Noise", "Already on " + mainline]
    lines += ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for r in reports:
        fixes, noisy = split_missing(r.missing, noise, key_re)
        keys = sorted({k for i in fixes for k in keys_in(i.commit.subject, key_re)})
        cells = (
            ["?"] * 4
            if r.problem
            else [str(len(fixes)), ", ".join(keys) or "-", str(len(noisy)), str(len(r.cleared))]
        )
        lines.append("| " + " | ".join(_md(c) for c in [r.service, r.ref, *cells]) + " |")

    for r in reports:
        lines += ["", f"## {r.service}", ""]
        if r.problem:
            lines.append(f"**Not checked:** {r.problem}")
            continue
        if not r.only_in_production:
            lines.append(f"Clean: everything {label} runs is on {mainline}.")
            continue
        fixes, noisy = split_missing(r.missing, noise, key_re)
        if fixes:
            lines.append(f"**Not on {mainline} ({len(fixes)}):**")
            for inv in fixes:
                keys = keys_in(inv.commit.subject, key_re)
                prefix = f"{', '.join(keys)}: " if keys else ""
                tags = f" (in tags: {', '.join(inv.tags)})" if inv.tags else ""
                lines.append(f"- {prefix}{_md(inv.commit.subject)} ({inv.commit.short}){tags}")
        if noisy:
            lines.append(f"Noise, not on {mainline} ({len(noisy)}):")
            lines += [f"- {_md(i.commit.subject)} ({i.commit.short})" for i in noisy]
        if r.cleared:
            lines.append(f"Already on {mainline}:")
            lines += [
                f"- {_md(i.commit.subject)} ({i.commit.short}): {i.equivalence}" for i in r.cleared
            ]
    return "\n".join(lines) + "\n"


def to_json(
    reports: Sequence[ServiceReport],
    label: str,
    mainline: str,
    noise: Sequence[re.Pattern[str]],
    key_re: re.Pattern[str],
) -> str:
    def entry(inv: Inverted) -> dict:
        return {
            "sha": inv.commit.sha,
            "subject": inv.commit.subject,
            "keys": keys_in(inv.commit.subject, key_re),
            "tags": inv.tags,
        }

    services = []
    for r in reports:
        fixes, noisy = split_missing(r.missing, noise, key_re)
        services.append(
            {
                "service": r.service,
                "ref": r.ref,
                "problem": r.problem,
                "missing": [entry(i) for i in fixes],
                "noise": [entry(i) for i in noisy],
                "already_on_mainline": [
                    {**entry(i), "equivalence": i.equivalence} for i in r.cleared
                ],
            }
        )
    payload = {
        "environment": label,
        "mainline": mainline,
        "headline": headline(reports, noise, key_re),
        "services": services,
    }
    return json.dumps(payload, indent=2) + "\n"


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="hotfix_mergeback",
        description="Commits running in production that mainline does not carry.",
    )
    p.add_argument("--production", required=True, help="version manifest of production")
    p.add_argument("--mainline", default="main", help="branch the next release is cut from")
    p.add_argument("--workspace", default=".", help="directory holding the service clones")
    p.add_argument("--noise-file", default=str(DEFAULT_NOISE_FILE))
    p.add_argument("--key-pattern", default=DEFAULT_KEY_PATTERN, help="regex for ticket keys")
    p.add_argument(
        "--no-fetch", action="store_true", help="skip git fetch and work from local refs"
    )
    p.add_argument("--json", action="store_true", help="JSON instead of markdown")
    p.add_argument(
        "--fail-on-missing",
        action="store_true",
        help="exit 2 when a non-noise commit is missing from mainline or a service is unchecked",
    )
    p.add_argument("-o", "--output", help="write the report here instead of stdout")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        key_re = re.compile(args.key_pattern)
        pins = load_pins(args.production)
        noise = load_noise(args.noise_file)
    except (InputError, re.error, OSError) as exc:
        print(f"hotfix_mergeback: {exc}", file=sys.stderr)
        return 1
    workspace = Path(args.workspace)
    reports = [
        analyse_service(workspace, pins[name], args.mainline, not args.no_fetch)
        for name in sorted(pins)
    ]
    label = Path(args.production).stem
    emit = to_json if args.json else render
    text = emit(reports, label, args.mainline, noise, key_re)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    head = headline(reports, noise, key_re)
    if args.fail_on_missing and (head["missing"] or head["services_not_checked"]):
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
