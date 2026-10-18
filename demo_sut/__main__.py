"""``python -m demo_sut [--host H] [--port N] [--db FILE]`` or ``python -m demo_sut openapi``."""

from __future__ import annotations

import argparse
import sys

from demo_sut import openapi
from demo_sut.server import DemoServer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m demo_sut", description=__doc__)
    sub = parser.add_subparsers(dest="command")
    spec = sub.add_parser("openapi", help="print (or --write) the OpenAPI document")
    spec.add_argument("--write", action="store_true", help="rewrite demo_sut/openapi.json")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765, help="0 picks a free port")
    parser.add_argument("--db", default=None, help="SQLite file (default: fresh temp file)")
    parser.add_argument("--verbose", action="store_true", help="log every request")
    args = parser.parse_args(argv)
    if args.command == "openapi":
        if args.write:
            print(openapi.write())
        else:
            sys.stdout.write(openapi.render())
        return 0
    server = DemoServer(args.host, args.port, args.db, quiet=not args.verbose)
    print(f"demo_sut listening on {server.base_url} (db {server.db_path})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
