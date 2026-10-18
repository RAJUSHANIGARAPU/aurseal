"""
Is the application under test up? One GET to the configured base URL.

    python -m tools.slash.reach [--url URL] [--env qa] [--timeout 10] [--accept 200,302]

Exit 0 when the answer's status is accepted, 1 otherwise (with the reason). Redirects
are not followed, so a login redirect (302) is seen as such.
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request


class _Stay(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def probe(
    url: str, timeout: float = 10.0, accept: tuple[int, ...] | None = None
) -> tuple[bool, str]:
    """(reachable, description). ``accept`` None means any status below 500."""
    opener = urllib.request.build_opener(_Stay)
    try:
        with opener.open(urllib.request.Request(url, method="GET"), timeout=timeout) as response:
            status = response.status
    except urllib.error.HTTPError as err:
        status = err.code
    except (urllib.error.URLError, OSError) as err:
        reason = getattr(err, "reason", err)
        return False, f"{url} is unreachable: {reason}"
    ok = status in accept if accept else status < 500
    return ok, f"{url} answered HTTP {status}"


def base_url(env: str | None) -> str:
    from core.config_reader import ConfigReader

    return ConfigReader(env).get_base_url()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reachability check of the app under test.")
    parser.add_argument("--url")
    parser.add_argument("--env")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--accept", help="comma-separated accepted statuses, e.g. 200,302")
    args = parser.parse_args(argv)
    accept = tuple(int(s) for s in args.accept.split(",")) if args.accept else None
    ok, detail = probe(args.url or base_url(args.env), args.timeout, accept)
    print(("up: " if ok else "down: ") + detail)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
