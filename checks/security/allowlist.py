"""Exemptions that expire.

``security/allowlist.yml``::

    entries:
      - fingerprint: "bandit:B324:test_data/allocator.py:1a2b3c4d5e6f"
        reason: "sha1 names a file; not a security use"
        expires: 2027-01-16

Every entry needs all three fields; a missing one is rejected when the file is loaded. An
expired entry is itself a blocking problem, so an exemption cannot become permanent by
neglect. An entry that matches nothing is reported (it can be deleted) but does not fail.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from checks.security.model import SecurityFinding


@dataclass(frozen=True)
class AllowEntry:
    fingerprint: str
    reason: str
    expires: date


@dataclass
class Judged:
    blocking: list[SecurityFinding] = field(default_factory=list)
    allowlisted: list[tuple[SecurityFinding, AllowEntry]] = field(default_factory=list)
    expired: list[AllowEntry] = field(default_factory=list)
    unused: list[AllowEntry] = field(default_factory=list)


@dataclass
class Allowlist:
    entries: list[AllowEntry] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path | str | None) -> Allowlist:
        if path is None or not Path(path).is_file():
            return cls()
        import yaml

        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        items = raw.get("entries") if isinstance(raw, dict) else None
        if items is None:
            return cls()
        if not isinstance(items, list):
            raise ValueError(f"{path}: 'entries' must be a list")
        entries = []
        for number, item in enumerate(items, 1):
            if not isinstance(item, dict):
                raise ValueError(f"{path}: entry {number} is not a mapping")
            missing = [k for k in ("fingerprint", "reason", "expires") if not item.get(k)]
            if missing:
                raise ValueError(f"{path}: entry {number} is missing {missing}")
            expires = item["expires"]
            if not isinstance(expires, date):
                try:
                    expires = date.fromisoformat(str(expires))
                except ValueError as exc:
                    raise ValueError(f"{path}: entry {number}: expires is not a date") from exc
            entries.append(
                AllowEntry(str(item["fingerprint"]).strip(), str(item["reason"]).strip(), expires)
            )
        fingerprints = [e.fingerprint for e in entries]
        duplicates = sorted({f for f in fingerprints if fingerprints.count(f) > 1})
        if duplicates:
            raise ValueError(f"{path}: duplicate fingerprints {duplicates}")
        return cls(entries)

    def apply(self, findings: Iterable[SecurityFinding], today: date) -> Judged:
        by_fp = {e.fingerprint: e for e in self.entries}
        judged = Judged()
        used: set[str] = set()
        for finding in findings:
            entry = by_fp.get(finding.fingerprint)
            if entry is None:
                judged.blocking.append(finding)
                continue
            used.add(entry.fingerprint)
            if entry.expires < today:
                judged.blocking.append(finding)
            else:
                judged.allowlisted.append((finding, entry))
        judged.expired = [e for e in self.entries if e.expires < today]
        judged.unused = [e for e in self.entries if e.fingerprint not in used]
        return judged
