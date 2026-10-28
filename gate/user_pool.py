"""A pool of login users, one leased per test process, so parallel workers never share one.

Two processes logging in as the same user step on each other: one login ends the
other's session, both write the same records, and every failed attempt counts
against the one account an identity provider is watching. With a pool, each process
holds a user of its own for as long as it lives.

The pool is a git-ignored file next to the per-environment ``.env.<env>`` file
(:mod:`core.env_file`), ``.env.<env>.pool``, in the same format::

    POOL_1_USERNAME=user1@example.test
    POOL_1_PASSWORD=...
    POOL_2_USERNAME=user2@example.test
    POOL_2_PASSWORD=...

``n`` orders the pool and may have gaps. A username without a password, or the
reverse, a duplicate username, or any other key is an error naming the line's key,
never its value.

**Leases.** A user is leased by taking an OS advisory lock (``flock``, exclusive,
non-blocking) on a lock file per user in the lease directory
(``~/.cache/aurseal/leases``, override with ``USER_POOL_LEASE_DIR``). The lock is held
for the life of the process and the OS drops it when the process ends, however it
ends, so a crash never leaves a stale lease behind. The lock file holds who has it
(pid, start time, shard, working directory) for whoever wonders.

Worker ``i`` of shard ``i/N`` (``TEST_SHARD``, exported by :mod:`gate.pytest_shard`)
asks for pool user ``i`` first, so it finds that user's cached session from the last
run, then takes any free one. When every user is leased it waits
(``USER_POOL_WAIT_S``, default 600 s, polling every ``USER_POOL_POLL_S``, 5 s, and
saying so once), then fails naming the usernames and the lock directory.

**Precedence** (wired into :class:`core.auth.settings.AuthSettings`): a username the
environment exports (``AUTH_USERNAME``, what CI and ``gate.credentials`` set) wins and
the pool is not touched; then the pool; then the ``.env.<env>`` file and the committed
configuration as before. Without a pool file nothing changes.

``python -m gate.user_pool size --env qa`` prints how many users the pool has, and
``workers --env qa [--requested N]`` how many parallel workers it allows. With
``USER_POOL_LEASE_RECORD`` set, the process also writes which user it leased to that
file (``gate.parallel_run`` reports them).
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from core.env_file import env_file_path, parse_env_text
from core.logger import logger

try:  # POSIX only; on other platforms the pool refuses to lease rather than share users.
    import fcntl
except ImportError:  # pragma: no cover - exercised on Windows only
    fcntl = None  # type: ignore[assignment]

POOL_SUFFIX = ".pool"
LEASE_DIR_ENV = "USER_POOL_LEASE_DIR"
WAIT_ENV = "USER_POOL_WAIT_S"
POLL_ENV = "USER_POOL_POLL_S"
LEASE_RECORD_ENV = "USER_POOL_LEASE_RECORD"
SHARD_ENV = "TEST_SHARD"
DEFAULT_WAIT_S = 600.0
DEFAULT_POLL_S = 5.0
MAX_DEFAULT_WORKERS = 4
EXIT_OK, EXIT_USAGE = 0, 2

_KEY = re.compile(r"^POOL_(\d+)_(USERNAME|PASSWORD)$")


class PoolError(ValueError):
    """The pool file is malformed; the message names keys, never values."""


class PoolExhausted(RuntimeError):
    """Every pool user stayed leased for the whole wait."""


@dataclass(frozen=True)
class PoolUser:
    index: int  # the n of POOL_<n>_*
    username: str
    password: str = field(repr=False)


def default_lease_dir() -> Path:
    override = os.environ.get(LEASE_DIR_ENV, "").strip()
    return Path(override) if override else Path.home() / ".cache" / "aurseal" / "leases"


def pool_file_path(env: str, directory: str | Path = ".") -> Path:
    """``<directory>/.env.<env>.pool``, next to the environment's ``.env`` file."""
    base = env_file_path(env, directory)
    return base.with_name(base.name + POOL_SUFFIX)


def parse_pool(values: Mapping[str, str]) -> list[PoolUser]:
    """Pool users from parsed ``KEY=value`` pairs, in ``n`` order. Raises PoolError."""
    pairs: dict[int, dict[str, str]] = {}
    for key, value in values.items():
        match = _KEY.match(key)
        if not match:
            raise PoolError(f"pool file: unexpected key {key} (want POOL_<n>_USERNAME/PASSWORD)")
        pairs.setdefault(int(match.group(1)), {})[match.group(2)] = value.strip()
    users: list[PoolUser] = []
    seen: dict[str, int] = {}
    for index in sorted(pairs):
        username, password = pairs[index].get("USERNAME"), pairs[index].get("PASSWORD")
        if not username:
            raise PoolError(f"pool file: POOL_{index}_PASSWORD has no POOL_{index}_USERNAME")
        if not password:
            raise PoolError(f"pool file: POOL_{index}_USERNAME has no POOL_{index}_PASSWORD")
        folded = username.lower()
        if folded in seen:
            raise PoolError(f"pool file: POOL_{index}_USERNAME repeats POOL_{seen[folded]}")
        seen[folded] = index
        users.append(PoolUser(index, username, password))
    return users


def load_pool(env: str, directory: str | Path = ".") -> list[PoolUser]:
    """The pool for ``env``; ``[]`` when there is no pool file."""
    path = pool_file_path(env, directory)
    if not path.is_file():
        return []
    return parse_pool(parse_env_text(path.read_text(encoding="utf-8")))


def preferred_position(shard: str | None) -> int | None:
    """1-based pool position shard ``K/N`` asks for first: ``K``; ``1`` for ``serial/N``."""
    if not shard or not shard.strip():
        return None
    from gate.shard import parse_shard

    try:
        spec = parse_shard(shard.strip())
    except ValueError:
        return None
    return 1 if spec.serial else spec.index


def lease_order(users: Sequence[PoolUser], preferred: int | None) -> list[PoolUser]:
    """The pool with the preferred position (1-based, wrapping) first, the rest in order."""
    if not users or preferred is None or preferred < 1:
        return list(users)
    first = (preferred - 1) % len(users)
    return [users[first], *users[:first], *users[first + 1 :]]


def lock_path(lease_dir: Path, username: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._@-]+", "_", username.strip().lower())[:60]
    digest = hashlib.sha1(
        username.strip().lower().encode("utf-8"), usedforsecurity=False
    ).hexdigest()[:8]
    return lease_dir / f"{safe}-{digest}.lock"


@dataclass
class Lease:
    """A held pool user. The lock lives as long as ``fd`` is open."""

    user: PoolUser
    path: Path
    fd: int | None = field(default=None, repr=False)

    def release(self) -> None:
        if self.fd is None:
            return
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)
            self.fd = None

    def __enter__(self) -> Lease:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def holder_info(shard: str | None = None) -> dict[str, object]:
    return {
        "pid": os.getpid(),
        "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "shard": shard or "",
        "cwd": os.getcwd(),
    }


def try_lease(
    users: Sequence[PoolUser],
    lease_dir: str | Path,
    preferred: int | None = None,
    holder: Mapping[str, object] | None = None,
) -> Lease | None:
    """Lock the first free user (preferred first); None when every user is held."""
    if fcntl is None:  # pragma: no cover - Windows
        raise PoolError("the user pool needs POSIX file locks (fcntl)")
    directory = Path(lease_dir)
    directory.mkdir(parents=True, exist_ok=True)
    for user in lease_order(users, preferred):
        path = lock_path(directory, user.username)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            if exc.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                continue
            raise
        info = {**(holder or holder_info()), "username": user.username}
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps(info, sort_keys=True).encode("utf-8"))
        return Lease(user, path, fd)
    return None


def acquire(
    users: Sequence[PoolUser],
    lease_dir: str | Path,
    *,
    preferred: int | None = None,
    wait_s: float = DEFAULT_WAIT_S,
    poll_s: float = DEFAULT_POLL_S,
    holder: Mapping[str, object] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Lease:
    """Lease a user, waiting up to ``wait_s`` while all are held. Raises PoolExhausted."""
    deadline = clock() + max(wait_s, 0.0)
    announced = False
    while True:
        lease = try_lease(users, lease_dir, preferred, holder)
        if lease is not None:
            return lease
        if clock() >= deadline:
            names = ", ".join(u.username for u in users)
            raise PoolExhausted(
                f"all {len(users)} pool user(s) stayed leased for {wait_s:.0f}s: {names}; "
                f"lock files in {lease_dir} name the holders"
            )
        if not announced:
            logger.info(
                "[pool] all %d user(s) leased; waiting up to %.0fs for one", len(users), wait_s
            )
            announced = True
        sleep(max(poll_s, 0.01))


def _float(environ: Mapping[str, str], name: str, default: float) -> float:
    try:
        return float(environ.get(name, "").strip() or default)
    except ValueError:
        return default


_process_lease: Lease | None = None


def lease_for_process(
    env: str, directory: str | Path, environ: Mapping[str, str] | None = None
) -> Lease | None:
    """This process's lease, taken on first call; None when ``env`` has no pool."""
    global _process_lease
    if _process_lease is not None:
        return _process_lease
    environ = os.environ if environ is None else environ
    users = load_pool(env, directory)
    if not users:
        return None
    shard = environ.get(SHARD_ENV, "").strip() or None
    lease = acquire(
        users,
        environ.get(LEASE_DIR_ENV, "").strip() or default_lease_dir(),
        preferred=preferred_position(shard),
        wait_s=_float(environ, WAIT_ENV, DEFAULT_WAIT_S),
        poll_s=_float(environ, POLL_ENV, DEFAULT_POLL_S),
        holder=holder_info(shard),
    )
    logger.info("[pool] leased pool user %d (%s)", lease.user.index, lease.user.username)
    record = environ.get(LEASE_RECORD_ENV, "").strip()
    if record:
        Path(record).parent.mkdir(parents=True, exist_ok=True)
        Path(record).write_text(
            json.dumps({"index": lease.user.index, "username": lease.user.username, "shard": shard})
            + "\n",
            encoding="utf-8",
        )
    _process_lease = lease
    return lease


def allowed_workers(pool_size: int, requested: int | None, needs_login: bool) -> int:
    """Parallel workers for a run: each worker that logs in needs a pool user of its own.

    Raises ValueError when ``requested`` cannot be honoured.
    """
    if requested is not None and requested < 1:
        raise ValueError(f"--workers must be >= 1, got {requested}")
    if not needs_login:
        return requested or MAX_DEFAULT_WORKERS
    limit = max(pool_size, 1)  # no pool: the one configured login, one worker
    if requested is None:
        return min(limit, MAX_DEFAULT_WORKERS)
    if requested > limit:
        source = f"the pool has {pool_size} user(s)" if pool_size else "there is no user pool"
        raise ValueError(
            f"{requested} workers need {requested} login users but {source}; "
            "add POOL_<n>_USERNAME/PASSWORD pairs or ask for fewer workers"
        )
    return requested


def _needs_login(env: str) -> bool:
    from core.auth.settings import LOGIN_MODES, resolve_auth_mode
    from core.config_reader import ConfigReader

    return resolve_auth_mode(ConfigReader(env)) in LOGIN_MODES


def main(argv: Sequence[str] | None = None, out=None, root: str | Path | None = None) -> int:
    out = out or sys.stdout
    root = Path(root) if root else Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(prog="python -m gate.user_pool")
    sub = parser.add_subparsers(dest="command", required=True)
    size = sub.add_parser("size", help="number of users in the pool")
    size.add_argument("--env", default=os.environ.get("TEST_ENV") or "qa")
    workers = sub.add_parser("workers", help="parallel workers the pool allows")
    workers.add_argument("--env", default=os.environ.get("TEST_ENV") or "qa")
    workers.add_argument("--requested", type=int, default=None)
    args = parser.parse_args(argv)

    try:
        users = load_pool(args.env, root)
    except PoolError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    if args.command == "size":
        print(len(users), file=out)
        return EXIT_OK
    try:
        print(allowed_workers(len(users), args.requested, _needs_login(args.env)), file=out)
    except ValueError as exc:
        print(f"workers: {exc}", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
