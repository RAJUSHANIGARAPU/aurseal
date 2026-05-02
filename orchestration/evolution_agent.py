"""Evolution agent — reads historical runs and proposes framework improvements."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from orchestration.storage import PluginStorage


class EvolutionAgent:
    """Analyzes plugin run history and generates improvement proposals via LLM."""

    def run(self, llm=None) -> dict:
        storage = PluginStorage()
        recent_runs = storage.get_recent_runs(n=4)

        if not recent_runs:
            return {"proposals": [], "reason": "no historical run data found"}

        run_summary = "\n".join(
            f"- run_id={r['id'][:8]} health={r['health_score']} "
            f"plugins={r['plugins_run']} cost=${r['cost_usd']:.4f}"
            for r in recent_runs
        )

        proposals: list[str] = []
        try:
            if llm is None:
                from api.llm.claude_client import ClaudeLLMClient

                llm = ClaudeLLMClient()

            prompt = (
                "You are a test framework evolution expert. "
                f"Here are the last {len(recent_runs)} plugin run summaries:\n\n"
                f"{run_summary}\n\n"
                "Generate exactly 3 concrete improvement proposals for this AI test framework. "
                "Format each as:\n## Proposal N: <title>\n<description>\n"
                "**Impact**: <impact>\n**Effort**: <effort>\n"
            )
            response = llm.complete(prompt)
            if response and not response.startswith("Claude API error"):
                proposals = [p.strip() for p in response.split("## Proposal") if p.strip()]
            else:
                proposals = [
                    "Proposal 1: Add retry analytics dashboard",
                    "Proposal 2: Implement cross-plugin correlation",
                    "Proposal 3: Add cost-aware scheduling",
                ]
        except Exception:  # noqa: BLE001
            proposals = [
                "Proposal 1: Add retry analytics dashboard",
                "Proposal 2: Implement cross-plugin correlation",
                "Proposal 3: Add cost-aware scheduling",
            ]

        # Write proposals to reports/evolution/
        date_str = datetime.now(tz=timezone.utc).strftime("%Y%m%d")
        out_dir = Path("reports/evolution")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"proposals_{date_str}.md"
        content = f"# Evolution Proposals — {date_str}\n\n" + "\n\n".join(
            f"## Proposal {i+1}: {p}" if not p.startswith("Proposal") else p
            for i, p in enumerate(proposals)
        )
        out_file.write_text(content, encoding="utf-8")

        return {
            "proposals": proposals,
            "run_history_count": len(recent_runs),
            "proposals_file": str(out_file),
        }


def main() -> int:
    agent = EvolutionAgent()
    result = agent.run()
    print(f"Generated {len(result['proposals'])} proposals -> {result.get('proposals_file')}")
    for p in result["proposals"]:
        print(f"  - {p[:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
