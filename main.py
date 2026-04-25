"""CLI entry point for the autonomous API testing system.

Usage:
    python main.py --collection api/postman/sample_collection.json --env qa --max-retries 3
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Autonomous multi-agent API testing system",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  python main.py --collection api/postman/sample_collection.json --max-retries 2"
        ),
    )
    parser.add_argument(
        "--collection",
        required=True,
        help="Path to Postman Collection v2.1 JSON file",
    )
    parser.add_argument(
        "--env",
        default="qa",
        choices=["qa", "staging", "prod"],
        help="Target environment (default: qa)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        dest="max_retries",
        help="Maximum heal-and-rerun cycles (default: 3)",
    )
    parser.add_argument(
        "--output-dir",
        default="generated_tests",
        dest="output_dir",
        help="Directory for generated test files (default: generated_tests)",
    )
    parser.add_argument(
        "--relax-status",
        action="store_true",
        dest="relax_status",
        help="Allow healing agent to relax strict status code assertions",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        dest="no_llm",
        help="Disable LLM features even if ANTHROPIC_API_KEY is set",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    collection_path = Path(args.collection)
    if not collection_path.exists():
        print(f"ERROR: Collection file not found: {collection_path}", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Collection : {collection_path}")
    print(f"Environment: {args.env}")
    print(f"Max retries: {args.max_retries}")
    print(f"Output dir : {output_dir}")

    llm = None
    if not args.no_llm:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key:
            from api.llm.claude_client import ClaudeLLMClient

            llm = ClaudeLLMClient(api_key=api_key)
            print("LLM        : claude-sonnet-4-6 (enabled)")
        else:
            print("LLM        : disabled (ANTHROPIC_API_KEY not set)")
    else:
        print("LLM        : disabled (--no-llm flag)")

    from api.agents.orchestrator import Orchestrator

    orchestrator = Orchestrator(
        collection_path=collection_path,
        output_dir=output_dir,
        max_retries=args.max_retries,
        llm=llm,
        relax_status=args.relax_status,
    )

    print("\nStarting orchestration...\n")
    result = orchestrator.run()

    print("\n" + "=" * 60)
    print("ORCHESTRATION RESULT")
    print("=" * 60)
    print(f"Success        : {result.success}")
    print(f"Total runs     : {result.total_runs}")
    print(f"Tests passed   : {result.final_pass_count}")
    print(f"Tests failed   : {result.final_fail_count}")
    print(f"Healing attempts: {result.healing_attempts}")
    print(f"Session ID     : {result.session_id}")
    if result.report_path:
        print(f"Report         : {result.report_path}")
    print("=" * 60)

    if not result.success and result.failure_analyses:
        print("\nFailure summary:")
        for analysis in result.failure_analyses:
            print(f"  [{analysis.category.value}] {analysis.test_name}: {analysis.root_cause}")

    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
