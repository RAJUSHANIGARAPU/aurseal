"""Fixtures for the messaging examples: a frozen clock and the outbound-messages client."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from api.clients.outbound_messages_client import OutboundMessagesClient
from tests.demo_examples.messaging import support


@pytest.fixture
def frozen(demo_control: Any) -> Any:
    demo_control.freeze(support.NOW)
    return demo_control


@pytest.fixture
def outbound(demo_http: Any, frozen: Any) -> OutboundMessagesClient:
    return OutboundMessagesClient(demo_http, timeout_s=10, poll_s=0.05)


@pytest.fixture(autouse=True)
def isolated_verdicts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Detector examples here switch their own faults on: keep their verdicts out of the run's
    evidence file, one file per test."""
    path = tmp_path / "verdicts" / "verdicts.jsonl"
    monkeypatch.setenv("VERDICTS_FILE", str(path))
    monkeypatch.delenv("KNOWN_DEFECTS_CLOSED", raising=False)
    return path
