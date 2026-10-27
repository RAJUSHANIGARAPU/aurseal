"""Defect status flowing back from the tracker, and defect drafts flowing out to it.

**Status.** ``StatusCache.sync()`` asks each tracker (by id prefix) for the state of the
ids the suite references, plus every open defect, once, and writes
``reports/traceability/defect-status.json``. With ``DEFECT_STATUS_FILE`` pointing at that
snapshot, ``known_defect`` and ``still_reproduces`` treat its closed ids as closed (the
``KNOWN_DEFECTS_CLOSED`` variable still adds to it). Rules, all on the conservative side:

* a tracker that cannot be read keeps the last snapshot's states;
* an id never seen is treated as open (a closed defect treated as open reports
  ``cleared``, which fails loudly; the reverse would hide a regression).

**Drafts.** ``draft_from_failure`` builds an issue body from a failure (bundle, message,
verdict row) with a fingerprint over the node id and the *normalised* failure signature,
so the same failure maps to the same issue across runs. Nothing is filed unless the
caller asks: ``--submit`` locally, or ``DEFECT_AUTOFILE=true`` on ``main`` in CI (off by
default), and an existing issue with the same fingerprint is reused, never duplicated.

A draft body is redacted before it is returned (bearer, basic, JWT and ``key=``/``token=``/
``password=`` values, plus the values of secret-named environment variables), names the
deployed build the run measured (or "not measured") and the CI run, and lists the issues it
may duplicate under "Possibly already filed": the defect ids the test links to, then up to
three open snapshot defects whose title shares at least three distinctive words with the
failure. A failure that is a confirmed known defect (its detector reported ``reproduces``
in the same run) gets no draft.

**Batched lookups.** A tracker with ``get_states_batch`` is asked in chunks of
``BATCH_SIZE``; a chunk the tracker rejects (``BadQuery``) is split in half until the
offending id stands alone, and that id is listed under ``missing``.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from integrations.trackers.base import (
    BadQuery,
    DefectDraft,
    DefectState,
    TrackerConfig,
    TrackerUnavailable,
    build,
)
from traceability._util import env_flag, read_json, trace_dir, utc_now, write_json

SNAPSHOT_ENV = "DEFECT_STATUS_FILE"  # read by verification.defect_detector.closed_defects
SNAPSHOT_NAME = "defect-status.json"
AUTOFILE_ENV = "DEFECT_AUTOFILE"
SYNC_ENV = "DEFECT_SYNC"
FINGERPRINT_TAG = "defect-fingerprint"
DEFAULT_LABELS = ("bug", "from-test-run")
BATCH_SIZE = 50
CANDIDATE_MIN_SHARED = 3
CANDIDATE_LIMIT = 3
_STOP_WORDS = frozenset(
    "that this with from were have been when then than there their they them will would "
    "should could into onto over under after before about which while where what does done "
    "expected actual assert assertion error failed failure equal value test true false none "
    "<uuid> <ts> <addr> <hex> <tmp>".split()
)

_VOLATILE = (
    (re.compile(r"\b[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?"), "<ts>"),
    (re.compile(r"\b0x[0-9a-f]+\b", re.I), "<addr>"),
    (re.compile(r"\b[0-9a-f]{12,}\b", re.I), "<hex>"),
    (re.compile(r"(/tmp|/private/var|/var/folders)/\S+"), "<tmp>"),
    (re.compile(r"\b\d+(\.\d+)?\b"), "<n>"),
)


def snapshot_path() -> Path:
    raw = os.environ.get(SNAPSHOT_ENV, "").strip()
    return Path(raw) if raw else trace_dir() / SNAPSHOT_NAME


def normalise_signature(text: str) -> str:
    """The failure message with volatile parts (ids, times, numbers, temp paths) masked."""
    out = " ".join(str(text or "").split())
    for pattern, token in _VOLATILE:
        out = pattern.sub(token, out)
    return out[:400]


def fingerprint(nodeid: str, signature: str) -> str:
    text = f"{nodeid}\n{normalise_signature(signature)}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------- status


@dataclass
class Snapshot:
    defects: dict[str, DefectState] = field(default_factory=dict)
    open_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    generated_at: str = ""
    missing: list[str] = field(default_factory=list)  # ids the tracker rejected even alone

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "generated_at": self.generated_at or utc_now(),
            "defects": {k: v.to_json() for k, v in sorted(self.defects.items())},
            "open": sorted(self.open_ids),
            "errors": self.errors,
            "missing": sorted(self.missing),
        }

    def age_line(self) -> str:
        from traceability.declared import age_line

        return age_line(self.generated_at)

    @classmethod
    def load(cls, path: Path) -> Snapshot:
        data = read_json(path)
        if not isinstance(data, dict):
            return cls()
        return cls(
            defects={k: DefectState.from_json(v) for k, v in (data.get("defects") or {}).items()},
            open_ids=list(data.get("open") or ()),
            errors=list(data.get("errors") or ()),
            generated_at=str(data.get("generated_at") or ""),
            missing=list(data.get("missing") or ()),
        )

    def closed_ids(self) -> set[str]:
        return {k for k, v in self.defects.items() if v.state == "closed"}

    def open_defects(self) -> list[dict[str, Any]]:
        return [self.defects[i].to_json() for i in sorted(self.open_ids) if i in self.defects]


class StatusCache:
    """Fetch defect states once, keep the last good answer when a tracker is down."""

    def __init__(
        self,
        config: TrackerConfig,
        snapshot: Path | None = None,
        *,
        session: Any = None,
        factory: Callable[[str, Mapping[str, Any], Any], Any] = build,
    ) -> None:
        self.config = config
        self.snapshot = Path(snapshot) if snapshot is not None else snapshot_path()
        self.session = session
        self.factory = factory
        self.current = Snapshot.load(self.snapshot)

    def sync(self, ids: Iterable[str] = ()) -> Snapshot:
        previous = Snapshot.load(self.snapshot)
        by_tracker: dict[str, list[str]] = {}
        for defect_id in sorted(set(ids)):
            name = self.config.tracker_for(defect_id)
            if name is not None:
                by_tracker.setdefault(name, []).append(defect_id)
        states = dict(previous.defects)
        open_ids = set(previous.open_ids)
        errors = []
        missing: list[str] = []
        for name, spec in self.config.trackers.items():
            try:
                tracker = self.factory(name, spec, self.session)
                fresh = lookup_states(tracker, by_tracker.get(name, []), missing)
                listed = tracker.list_open()
            except (TrackerUnavailable, ValueError, OSError) as exc:
                errors.append(f"{name}: {exc} (kept the last snapshot)")
                continue
            states.update(
                {k: v for k, v in fresh.items() if v.state != "unknown" or k not in states}
            )
            open_ids = {i for i in open_ids if self.config.tracker_for(i) != name}
            for ref in listed:
                states[ref.id] = ref
                open_ids.add(ref.id)
            open_ids |= {k for k, v in fresh.items() if v.state == "open"}
        self.current = Snapshot(states, sorted(open_ids), errors, utc_now(), sorted(missing))
        write_json(self.snapshot, self.current.to_json())
        return self.current

    def is_open(self, defect_id: str) -> bool:
        from verification.defect_detector import CLOSED_ENV

        env_closed = {c.strip() for c in os.environ.get(CLOSED_ENV, "").split(",") if c.strip()}
        if defect_id in env_closed:
            return False
        state = self.current.defects.get(defect_id)
        return state is None or state.state != "closed"


def lookup_states(
    tracker: Any, ids: Sequence[str], missing: list[str], size: int = BATCH_SIZE
) -> dict[str, DefectState]:
    """States for ``ids``: batched when the tracker can, bisecting a rejected batch."""
    batch = getattr(tracker, "get_states_batch", None)
    if batch is None:
        return tracker.get_states(ids)
    out: dict[str, DefectState] = {}

    def ask(chunk: Sequence[str]) -> None:
        if not chunk:
            return
        try:
            out.update(batch(list(chunk)))
        except BadQuery:
            if len(chunk) == 1:
                missing.append(chunk[0])
                return
            half = len(chunk) // 2
            ask(chunk[:half])
            ask(chunk[half:])

    ids = list(ids)
    for start in range(0, len(ids), max(size, 1)):
        ask(ids[start : start + size])
    return out


def open_defects_from_snapshot(path: Path | None = None) -> Callable[[], list[dict[str, Any]]]:
    """A provider for plans and the release verdict: open defects from the last sync.

    Raises when no snapshot exists, which those callers turn into "no evidence", never
    into "zero open defects". ``provider.generated_at()`` says how old the answer is.
    """

    def target() -> Path:
        return Path(path or snapshot_path())

    def provider() -> list[dict[str, Any]]:
        if not target().is_file():
            raise FileNotFoundError(f"no defect snapshot at {target()}: run `defects sync`")
        return Snapshot.load(target()).open_defects()

    def generated_at() -> str | None:
        return Snapshot.load(target()).generated_at or None if target().is_file() else None

    provider.generated_at = generated_at  # type: ignore[attr-defined]
    return provider


def defects_provider(
    rows: Sequence[Mapping[str, Any]], generated_at: str | None
) -> Callable[[], list[dict[str, Any]]]:
    """A provider over fixed rows with a stated age (tests, the platform service)."""

    def provider() -> list[dict[str, Any]]:
        return [dict(r) for r in rows]

    provider.generated_at = lambda: generated_at  # type: ignore[attr-defined]
    return provider


def refresh_declared(config: TrackerConfig, ids: Iterable[str], out: Path) -> Snapshot:
    """Ask the trackers about exactly ``ids`` and write a snapshot to commit.

    An id no tracker owns still gets an entry (state ``unknown``), so the offline check can
    tell "asked, no tracker knows it" from "never asked".
    """
    wanted = sorted(set(ids))
    if Path(out).is_file():
        Path(out).unlink()  # a committed snapshot holds this answer only, nothing inherited
    cache = StatusCache(config, Path(out))
    snap = cache.sync(wanted)
    for defect_id in wanted:
        snap.defects.setdefault(defect_id, DefectState(defect_id, "unknown"))
    snap.defects = {k: v for k, v in snap.defects.items() if k in set(wanted)}
    snap.open_ids = [i for i in snap.open_ids if i in set(wanted)]
    write_json(Path(out), snap.to_json())
    return snap


# ---------------------------------------------------------------------- drafts


def _bundle(path: Path | None) -> dict[str, Any]:
    data = read_json(path) if path is not None else None
    return data if isinstance(data, dict) else {}


def _words(text: str) -> set[str]:
    words = re.findall(r"[a-z][a-z0-9]{3,}", normalise_signature(text).lower())
    return {w for w in words if w not in _STOP_WORDS}


def candidates(
    snapshot: Snapshot | None,
    nodeid_links: Sequence[str],
    message: str,
    *,
    min_shared: int = CANDIDATE_MIN_SHARED,
    limit: int = CANDIDATE_LIMIT,
) -> list[str]:
    """Issues this failure may duplicate, offline: linked ids, then similar open ones.

    Similar means the open defect's title shares at least ``min_shared`` words of four or
    more letters (stop words excluded) with the normalised failure message; the top
    ``limit`` by token-set (Jaccard) similarity are kept.
    """
    out = list(dict.fromkeys(i for i in nodeid_links if i))
    if snapshot is None:
        return out
    failure = _words(message)
    scored = []
    for defect_id in snapshot.open_ids:
        state = snapshot.defects.get(defect_id)
        if state is None or defect_id in out:
            continue
        title = _words(state.title)
        shared = failure & title
        if len(shared) >= min_shared:
            scored.append((len(shared) / len(failure | title), defect_id, state.title))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return out + [f"{d} ({t})" for _, d, t in scored[:limit]]


def run_url(environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    server, repo, run = (
        env.get(k, "") for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID")
    )
    return f"{server}/{repo}/actions/runs/{run}" if server and repo and run else "not in CI"


def draft_from_failure(
    bundle: Path | None,
    verdict_row: Mapping[str, Any] | None = None,
    *,
    nodeid: str = "",
    run_id: str = "",
    message: str = "",
    labels: Sequence[str] = DEFAULT_LABELS,
    snapshot: Snapshot | None = None,
    links: Sequence[str] = (),
    environ: Mapping[str, str] | None = None,
) -> DefectDraft:
    """An issue draft for one failure, redacted. Never filed here."""
    from core.build_info import last_observed
    from gate.notify import redact_secrets

    data = _bundle(bundle)
    row = dict(verdict_row or {})
    nodeid = nodeid or str(row.get("nodeid") or data.get("test") or "")
    signature = message or str(data.get("error") or row.get("detail") or "")
    kind = "closed_regressed" if row.get("verdict") == "closed_regressed" else "failure"
    fp = fingerprint(nodeid, signature)
    short = (signature.splitlines() or ["failure"])[0][:80]
    test = nodeid.rsplit("::", 1)[-1] or "test"
    if kind == "closed_regressed":
        title = f"Regression of {row.get('ticket')}: {test}"
    else:
        title = f"{test}: {short}"
    lines = [
        f"**Test:** `{nodeid}`",
        f"**Run:** `{run_id or data.get('context', {}).get('runId', '')}`",
        "",
        "**Failure**",
        "",
        "```",
        signature.strip()[:3000] or "(no message recorded)",
        "```",
    ]
    if row:
        lines += ["", f"**Verdict:** {row.get('verdict')} on {row.get('ticket') or '-'}"]
    for key, title_ in (("failedRequests", "Failed requests"), ("consoleErrors", "Console")):
        if data.get(key):
            lines += ["", f"**{title_}**", ""] + [f"- `{x}`" for x in list(data[key])[:10]]
    deployed = data.get("context", {}).get("deployedVersion") or last_observed().describe()
    lines += [
        "",
        f"**Deployed build:** {deployed}",
        f"**CI run:** {run_url(environ)}",
    ]
    if snapshot is not None:
        lines.append(f"**Defect status:** {snapshot.age_line()}")
    linked = list(links) + ([str(row["ticket"])] if row.get("ticket") else [])
    possible = candidates(snapshot, linked, signature)
    if possible:
        lines += ["", "**Possibly already filed**", ""] + [f"- {c}" for c in possible]
    lines += ["", f"<!-- {FINGERPRINT_TAG}: {fp} -->"]
    tags = tuple(labels) + (("regression",) if kind == "closed_regressed" else ())
    body = redact_secrets("\n".join(lines), environ=environ)
    return DefectDraft(redact_secrets(title, environ=environ), body, fp, tags, nodeid, run_id, kind)


@dataclass
class DraftOutcome:
    draft: DefectDraft
    action: str  # drafted | exists | filed | skipped
    ref: str | None = None
    note: str = ""


def failure_links(reports: Path) -> dict[str, list[str]]:
    """Per node id, the defect ids the test is linked to in a run (marker or verdict row)."""
    from traceability.evidence import ArtifactResultSource

    src = ArtifactResultSource(reports)
    out: dict[str, list[str]] = {}
    for res in src.results():
        if res.defect:
            out.setdefault(res.nodeid, []).append(res.defect)
    for v in src.verdicts():
        if v.get("kind") == "expected_red" and v.get("ticket") and v.get("nodeid"):
            out.setdefault(str(v["nodeid"]), []).append(str(v["ticket"]))
    return {k: list(dict.fromkeys(v)) for k, v in out.items()}


def failures_from_reports(reports: Path) -> list[tuple[str, str, str, dict[str, Any] | None]]:
    """``(run_id, nodeid, message, verdict_row)`` per failure in a run's artefacts.

    Failures are failed/error outcomes from the run-trust markers and
    ``closed_regressed`` detector rows; messages come from failure bundles when one names
    the test. A failure whose detector reported ``reproduces`` in the same run is a
    confirmed known defect and gets no draft.
    """
    from traceability.evidence import ArtifactResultSource

    src = ArtifactResultSource(reports)
    bundles: dict[str, str] = {}
    for path in sorted(Path(reports).rglob("failures/*.json")):
        data = _bundle(path)
        if data.get("test"):
            bundles[str(data["test"])] = str(data.get("error") or "")
    out = []
    regressed = {
        (v["run_id"], v["nodeid"]): v
        for v in src.verdicts()
        if v.get("kind") == "expected_red" and v.get("verdict") == "closed_regressed"
    }
    confirmed = {
        (v.get("run_id"), v.get("nodeid"))
        for v in src.verdicts()
        if v.get("kind") == "expected_red" and v.get("verdict") == "reproduces"
    }
    for res in src.results():
        if res.outcome not in ("failed", "error"):
            continue
        if (res.run_id, res.nodeid) in confirmed:
            continue
        name = res.nodeid.rsplit("::", 1)[-1]
        message = bundles.get(res.nodeid) or bundles.get(name) or ""
        row = regressed.pop((res.run_id, res.nodeid), None)
        if row and not message:
            message = str(row.get("detail") or "")
        out.append((res.run_id, res.nodeid, message, row))
    for (run_id, nodeid), row in regressed.items():
        out.append((run_id, nodeid, str(row.get("detail") or ""), row))
    return out


def autofile_allowed(env: Mapping[str, str] | None = None) -> bool:
    """Auto-filing needs ``DEFECT_AUTOFILE=true`` AND a push to ``main``. Off by default."""
    env = os.environ if env is None else env
    return env_flag(AUTOFILE_ENV, False, dict(env)) and env.get("GITHUB_REF") == "refs/heads/main"


def file_drafts(
    drafts: Sequence[DefectDraft],
    tracker: Any | None,
    *,
    submit: bool,
    exclude: Iterable[str] = (),
) -> list[DraftOutcome]:
    """Dedup by fingerprint, then file only when ``submit``; flaky tests are never filed."""
    skip = set(exclude)
    out = []
    for draft in drafts:
        if draft.nodeid in skip:
            out.append(DraftOutcome(draft, "skipped", note="flaky: never auto-filed"))
            continue
        if not submit or tracker is None:
            out.append(DraftOutcome(draft, "drafted"))
            continue
        existing = tracker.find_by_fingerprint(draft.fingerprint)
        if existing is not None:
            out.append(DraftOutcome(draft, "exists", existing.id))
            continue
        ref = tracker.create(draft)
        out.append(DraftOutcome(draft, "filed", ref.id))
    return out


def write_drafts(outcomes: Sequence[DraftOutcome], directory: Path) -> list[Path]:
    paths = []
    for outcome in outcomes:
        d = outcome.draft
        path = Path(directory) / f"{d.fingerprint}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        header = f"<!-- action: {outcome.action}{' ' + outcome.ref if outcome.ref else ''} -->\n"
        path.write_text(f"{header}# {d.title}\n\n{d.body}\n", encoding="utf-8")
        paths.append(path)
    write_json(
        Path(directory) / "drafts.json",
        [{**o.draft.to_json(), "action": o.action, "ref": o.ref, "note": o.note} for o in outcomes],
    )
    return paths


# ---------------------------------------------------------------------- pytest wiring


def register(config) -> None:
    """With ``DEFECT_SYNC=true``, sync once on the controller before workers start.

    Register it before the known_defect plugin so that plugin reads the fresh snapshot.
    """
    if hasattr(config, "workerinput") or not env_flag(SYNC_ENV):
        return
    cache = StatusCache(TrackerConfig.load(rootdir=Path(str(config.rootpath))))
    snapshot = cache.sync(referenced_ids())
    os.environ.setdefault(SNAPSHOT_ENV, str(cache.snapshot))
    for error in snapshot.errors:
        print(f"[defects] {error}")


def referenced_ids() -> list[str]:
    """Defect ids the verdict file has seen (the suite's known_defect ids come via collect)."""
    from verification.verdicts import read_verdicts

    return sorted({str(v["ticket"]) for v in read_verdicts() if v.get("ticket")})


__all__ = [
    "AUTOFILE_ENV",
    "SNAPSHOT_ENV",
    "DraftOutcome",
    "Snapshot",
    "StatusCache",
    "autofile_allowed",
    "draft_from_failure",
    "failures_from_reports",
    "file_drafts",
    "fingerprint",
    "normalise_signature",
    "open_defects_from_snapshot",
    "write_drafts",
]
