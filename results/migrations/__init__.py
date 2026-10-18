"""Forward-only, numbered SQL migrations per domain.

Layout: ``results/migrations/<domain>/NNNN_<desc>.sql``. ``core`` applies first, other
domains alphabetically. A domain's first file may declare ``-- requires: core>=N`` (or
any other domain) on its first line; the requirement is checked before anything runs.

Each applied file is recorded in ``schema_migrations`` with its SHA-256. When an applied
file's checksum no longer matches, the store refuses to open: editing an applied
migration is how two databases silently diverge, so it must be a new migration instead.

Variants: a file may have one conditional sibling ``NNNN_<desc>.<variant>.sql``, used
instead of the base file when the variant's predicate holds for the connection (for
example ``nofts`` when SQLite lacks FTS5). The applied file's name is recorded, so the
checksum check compares against the file that actually ran.

Domains other than ``core`` must be listed in ``results/migrations/DOMAINS`` (or added
with :func:`register_domain`) together with the table prefix they own, and may only
create tables carrying that prefix.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from results._util import utc_now

HERE = Path(__file__).resolve().parent
CORE = "core"
_FILE = re.compile(r"^(\d{4})_([a-z0-9_]+?)(?:\.([a-z0-9]+))?\.sql$")
_REQUIRES = re.compile(r"^--\s*requires:\s*(.+)$", re.IGNORECASE)
_REQ_ITEM = re.compile(r"^\s*([a-z0-9_]+)\s*>=\s*(\d+)\s*$")
_CREATE_TABLE = re.compile(
    r"CREATE\s+(?:VIRTUAL\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`]?(\w+)", re.IGNORECASE
)
_ALTER_TABLE = re.compile(r"ALTER\s+TABLE\s+[\"`]?(\w+)", re.IGNORECASE)


class MigrationError(RuntimeError):
    """A migration cannot be applied, or an applied one was changed."""


@dataclass(frozen=True)
class AppliedMigration:
    domain: str
    version: int
    name: str
    sha256: str


@dataclass(frozen=True)
class MigrationState:
    domain: str
    version: int
    name: str
    applied: bool
    applied_at: str | None
    checksum_ok: bool | None


@dataclass(frozen=True)
class _File:
    domain: str
    version: int
    name: str
    path: Path

    @property
    def text(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


def fts5_available(conn: sqlite3.Connection) -> bool:
    """Whether this SQLite build has the FTS5 extension."""
    try:
        conn.execute("CREATE VIRTUAL TABLE temp.__fts5_probe USING fts5(x)")
        conn.execute("DROP TABLE temp.__fts5_probe")
        return True
    except sqlite3.OperationalError:
        return False


VARIANTS: dict[str, Callable[[sqlite3.Connection], bool]] = {
    "nofts": lambda conn: not fts5_available(conn),
}

_extra_domains: dict[str, tuple[Path, str]] = {}


def register_domain(name: str, path: Path, prefix: str | None = None) -> None:
    """Add a domain whose migrations live outside this package (prefix defaults to name)."""
    if name == CORE:
        raise MigrationError("the core domain cannot be re-registered")
    _extra_domains[name] = (Path(path), prefix or name)


def unregister_domain(name: str) -> None:
    """Forget a :func:`register_domain` call (an extension that registers only while it
    applies its own migrations, so other stores opened in the same process are unaffected)."""
    _extra_domains.pop(name, None)


def registered_prefixes() -> dict[str, str]:
    """``domain -> table prefix`` from the DOMAINS file plus :func:`register_domain` calls."""
    prefixes: dict[str, str] = {}
    for line in (HERE / "DOMAINS").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        prefixes[parts[0]] = parts[1] if len(parts) > 1 else parts[0]
    for name, (_, prefix) in _extra_domains.items():
        prefixes[name] = prefix
    return prefixes


def _domain_dirs() -> dict[str, Path]:
    dirs = {
        p.name: p
        for p in sorted(HERE.iterdir())
        if p.is_dir() and not p.name.startswith(("_", ".")) and any(p.glob("*.sql"))
    }
    for name, (path, _) in _extra_domains.items():
        dirs[name] = path
    return dirs


def _ordered(domains: Sequence[str] | None) -> list[str]:
    available = _domain_dirs()
    known = registered_prefixes()
    for name in available:
        if name not in known:
            raise MigrationError(
                f"migration domain {name!r} is not listed in results/migrations/DOMAINS"
            )
    wanted = list(available) if domains is None else list(domains)
    missing = [d for d in wanted if d not in available]
    if missing:
        raise MigrationError(f"unknown migration domain(s): {', '.join(missing)}")
    rest = sorted(d for d in wanted if d != CORE)
    return ([CORE] if CORE in wanted or domains is None else []) + rest


def _files(domain: str, conn: sqlite3.Connection) -> list[_File]:
    directory = _domain_dirs()[domain]
    by_version: dict[int, dict[str | None, Path]] = {}
    for path in sorted(directory.glob("*.sql")):
        match = _FILE.match(path.name)
        if not match:
            raise MigrationError(f"badly named migration file: {path}")
        version, variant = int(match.group(1)), match.group(3)
        slot = by_version.setdefault(version, {})
        if variant in slot:
            raise MigrationError(f"two {domain} migrations share version {version}")
        if variant is not None and variant not in VARIANTS:
            raise MigrationError(f"unknown migration variant {variant!r}: {path}")
        slot[variant] = path
    out: list[_File] = []
    for version in sorted(by_version):
        slot = by_version[version]
        if None not in slot:
            raise MigrationError(f"{domain} migration {version} has a variant but no base file")
        chosen = slot[None]
        for variant, path in slot.items():
            if variant is not None and VARIANTS[variant](conn):
                chosen = path
                break
        out.append(_File(domain, version, chosen.name, chosen))
    return out


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " domain TEXT NOT NULL, version INTEGER NOT NULL, name TEXT NOT NULL,"
        " sha256 TEXT NOT NULL, applied_at TEXT NOT NULL, PRIMARY KEY (domain, version))"
    )


def _applied(conn: sqlite3.Connection) -> dict[tuple[str, int], tuple[str, str, str]]:
    rows = conn.execute("SELECT domain, version, name, sha256, applied_at FROM schema_migrations")
    return {(d, v): (n, s, a) for d, v, n, s, a in rows}


def statements(sql: str) -> list[str]:
    """Split a script into complete statements (triggers and strings included)."""
    out: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            stmt = buffer.strip()
            if stmt and not all(
                part.strip().startswith("--") or not part.strip() for part in stmt.splitlines()
            ):
                out.append(stmt)
            buffer = ""
    if buffer.strip() and not all(
        part.strip().startswith("--") or not part.strip() for part in buffer.splitlines()
    ):
        raise MigrationError(f"incomplete SQL statement: {buffer.strip()[:80]}")
    return out


def _check_prefix(file: _File, sql: str) -> None:
    if file.domain == CORE:
        return
    prefix = registered_prefixes()[file.domain]
    for table in _CREATE_TABLE.findall(sql):
        if not table.startswith(prefix + "_"):
            raise MigrationError(
                f"{file.path.name}: domain {file.domain!r} may only create '{prefix}_*' "
                f"tables, not {table!r}"
            )
    for table in _ALTER_TABLE.findall(sql):
        if not table.startswith(prefix + "_"):
            raise MigrationError(f"{file.path.name}: domain {file.domain!r} altered {table!r}")


def _requires(file: _File) -> list[tuple[str, int]]:
    first = file.text.splitlines()[0] if file.text else ""
    match = _REQUIRES.match(first.strip())
    if not match:
        return []
    reqs = []
    for item in match.group(1).split(","):
        req = _REQ_ITEM.match(item)
        if not req:
            raise MigrationError(f"{file.path.name}: unreadable requires line {first!r}")
        reqs.append((req.group(1), int(req.group(2))))
    return reqs


def verify(conn: sqlite3.Connection) -> None:
    """Refuse a database whose applied migrations no longer match their files."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    if exists is None:
        return
    for (domain, _version), (name, sha, _) in _applied(conn).items():
        if domain not in _domain_dirs():
            continue  # a domain this checkout does not ship: not ours to judge
        path = _domain_dirs()[domain] / name
        if not path.is_file():
            raise MigrationError(f"applied migration {domain}/{name} is missing from disk")
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise MigrationError(
                f"applied migration {domain}/{name} was edited after it ran (checksum "
                "mismatch); add a new migration instead"
            )


def apply(conn: sqlite3.Connection, domains: Sequence[str] | None = None) -> list[AppliedMigration]:
    """Apply every pending migration; each file runs in its own transaction."""
    _ensure_table(conn)
    verify(conn)
    done: list[AppliedMigration] = []
    for domain in _ordered(domains):
        for file in _files(domain, conn):
            applied = _applied(conn)
            if (domain, file.version) in applied:
                continue
            sql = file.text
            _check_prefix(file, sql)
            for req_domain, req_version in _requires(file):
                have = max(
                    (v for (d, v) in applied if d == req_domain),
                    default=0,
                )
                if have < req_version:
                    raise MigrationError(
                        f"{domain}/{file.name} requires {req_domain}>={req_version}, "
                        f"database has {req_domain} {have}"
                    )
            in_tx = conn.in_transaction
            if not in_tx:
                conn.execute("BEGIN")
            try:
                for stmt in statements(sql):
                    conn.execute(stmt)
                conn.execute(
                    "INSERT INTO schema_migrations (domain, version, name, sha256, applied_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (domain, file.version, file.name, file.sha256, utc_now()),
                )
                if domain == CORE:
                    conn.execute(f"PRAGMA user_version = {int(file.version)}")
                if not in_tx:
                    conn.execute("COMMIT")
            except Exception as exc:
                if not in_tx:
                    conn.execute("ROLLBACK")
                raise MigrationError(f"{domain}/{file.name} failed: {exc}") from exc
            done.append(AppliedMigration(domain, file.version, file.name, file.sha256))
    return done


def status(conn: sqlite3.Connection) -> list[MigrationState]:
    """Every known migration file, applied or not, plus whether its checksum still matches."""
    _ensure_table(conn)
    applied = _applied(conn)
    out: list[MigrationState] = []
    for domain in _ordered(None):
        for file in _files(domain, conn):
            row = applied.get((domain, file.version))
            if row is None:
                out.append(MigrationState(domain, file.version, file.name, False, None, None))
                continue
            name, sha, at = row
            path = _domain_dirs()[domain] / name
            ok = path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == sha
            out.append(MigrationState(domain, file.version, name, True, at, ok))
    return out


__all__ = [
    "AppliedMigration",
    "MigrationError",
    "MigrationState",
    "VARIANTS",
    "apply",
    "fts5_available",
    "register_domain",
    "registered_prefixes",
    "statements",
    "status",
    "unregister_domain",
    "verify",
]
