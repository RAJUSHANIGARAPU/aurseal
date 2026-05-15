"""Anthropic tool_use agentic loop — base class for all agents in this project."""

from __future__ import annotations

import dataclasses
import json
import os
from collections.abc import Callable
from typing import Any

import requests


@dataclasses.dataclass
class AgentResult:
    status: str  # "passed" | "failed" | "blocked" | "done"
    reason: str
    actions: list[str]  # human-readable log of each tool call


_DONE_SCHEMA: dict = {
    "name": "done",
    "description": (
        "Signal that the task is complete or no further progress can be made. "
        "Call this when all failures are addressed or when you are stuck."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["passed", "failed", "blocked"],
                "description": (
                    "passed=fixes applied and likely to succeed, "
                    "failed=tried all tools but could not fix, "
                    "blocked=missing API key or unrecoverable error"
                ),
            },
            "reason": {
                "type": "string",
                "description": "One sentence explaining the outcome.",
            },
        },
        "required": ["status", "reason"],
    },
}


class BaseAgent:
    """
    Implements the Anthropic tool_use agentic loop.

    Subclasses call register_tool() in __init__ to expose capabilities,
    then call run() to let Claude decide what to do.
    """

    def __init__(self, model: str = "claude-sonnet-4-6") -> None:
        self.model = model
        self._api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
        self._tools: dict[str, tuple[Callable[..., Any], dict]] = {}

    def register_tool(self, name: str, fn: Callable[..., Any], schema: dict) -> None:
        """Register a callable with its Anthropic tool schema. Schema must include 'name'."""
        self._tools[name] = (fn, schema)

    def run(
        self,
        system_prompt: str,
        user_message: str,
        max_turns: int = 10,
    ) -> AgentResult:
        if not self._api_key:
            return AgentResult(status="blocked", reason="ANTHROPIC_API_KEY not set", actions=[])

        tool_schemas = [schema for _, schema in self._tools.values()] + [_DONE_SCHEMA]
        messages: list[dict] = [{"role": "user", "content": user_message}]
        actions: list[str] = []

        for _ in range(max_turns):
            response = self._call_claude(messages, tool_schemas, system_prompt)
            if "error" in response:
                return AgentResult(status="blocked", reason=response["error"], actions=actions)

            stop_reason = response.get("stop_reason", "")
            content: list[dict] = response.get("content", [])
            messages.append({"role": "assistant", "content": content})

            if stop_reason == "end_turn":
                text = next((b.get("text", "") for b in content if b.get("type") == "text"), "")
                return AgentResult(
                    status="blocked",
                    reason=text[:500] or "model returned text without calling done",
                    actions=actions,
                )

            if stop_reason != "tool_use":
                return AgentResult(
                    status="blocked",
                    reason=f"unexpected stop_reason: {stop_reason!r}",
                    actions=actions,
                )

            tool_results: list[dict] = []
            for block in content:
                if block.get("type") != "tool_use":
                    continue
                name = block["name"]
                inputs = block.get("input", {})
                use_id = block["id"]

                if name == "done":
                    return AgentResult(
                        status=inputs.get("status", "done"),
                        reason=inputs.get("reason", ""),
                        actions=actions,
                    )

                result_str = self._dispatch(name, inputs)
                actions.append(f"{name}({json.dumps(inputs)[:120]}) → {result_str[:120]}")
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": use_id, "content": result_str}
                )

            messages.append({"role": "user", "content": tool_results})

        return AgentResult(
            status="failed",
            reason=f"reached max_turns ({max_turns}) without calling done",
            actions=actions,
        )

    def _call_claude(self, messages: list[dict], tools: list[dict], system_prompt: str) -> dict:
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": 4096,
                    "system": system_prompt,
                    "tools": tools,
                    "tool_choice": {"type": "auto"},
                    "messages": messages,
                },
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"error": str(e)}

    def _dispatch(self, name: str, inputs: dict) -> str:
        if name not in self._tools:
            return json.dumps({"error": f"unknown tool: {name!r}"})
        fn, _ = self._tools[name]
        try:
            result = fn(**inputs)
            return result if isinstance(result, str) else json.dumps(result)
        except Exception as e:
            return json.dumps({"error": str(e)})
