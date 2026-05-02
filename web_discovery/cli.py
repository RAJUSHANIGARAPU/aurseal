"""CLI entry point: python -m web_discovery.cli <target_url>"""

from __future__ import annotations

import argparse
import logging
import os
import sys


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m web_discovery.cli",
        description="Autonomous web app discovery & test generation engine",
    )
    p.add_argument("target_url", nargs="?", help="Target URL to crawl (overrides WD_TARGET_URL)")
    p.add_argument("--depth", type=int, default=None, help="Max crawl depth (WD_MAX_DEPTH)")
    p.add_argument("--pages", type=int, default=None, help="Max pages (WD_MAX_PAGES)")
    p.add_argument("--output", default=None, help="Output dir for generated tests (WD_OUTPUT_DIR)")
    p.add_argument("--headful", action="store_true", help="Show browser window (non-headless)")
    p.add_argument("--auth-url", default=None, help="Auth URL (WD_AUTH_URL)")
    p.add_argument("--username", default=None, help="Auth username (WD_AUTH_USERNAME)")
    p.add_argument("--password", default=None, help="Auth password (WD_AUTH_PASSWORD)")
    p.add_argument("--no-robots", action="store_true", help="Ignore robots.txt")
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    # Push CLI args into env so DiscoveryConfig.from_env() picks them up
    os.environ["ENABLE_WEB_DISCOVERY"] = "true"
    if args.target_url:
        os.environ["WD_TARGET_URL"] = args.target_url
    if args.depth is not None:
        os.environ["WD_MAX_DEPTH"] = str(args.depth)
    if args.pages is not None:
        os.environ["WD_MAX_PAGES"] = str(args.pages)
    if args.output:
        os.environ["WD_OUTPUT_DIR"] = args.output
    if args.headful:
        os.environ["WD_HEADLESS"] = "false"
    if args.auth_url:
        os.environ["WD_AUTH_URL"] = args.auth_url
    if args.username:
        os.environ["WD_AUTH_USERNAME"] = args.username
    if args.password:
        os.environ["WD_AUTH_PASSWORD"] = args.password
    if args.no_robots:
        os.environ["WD_RESPECT_ROBOTS"] = "false"

    target = os.getenv("WD_TARGET_URL", "")
    if not target:
        print("ERROR: provide a target URL as an argument or set WD_TARGET_URL", file=sys.stderr)
        return 1

    try:
        from web_discovery.orchestrator import DiscoveryOrchestrator

        result = DiscoveryOrchestrator().run()
        print(result.summary())
        print(f"Graph:  {result.graph_path}")
        print(f"Tests:  {len(result.generated_files)} file(s)")
        for f in result.generated_files:
            print(f"  {f}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        logging.getLogger(__name__).debug("traceback", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
