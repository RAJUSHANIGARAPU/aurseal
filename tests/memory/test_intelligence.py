"""Tests for memory.intelligence.FailureIntelligenceEngine."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from unittest.mock import MagicMock

from memory.config import MemoryConfig
from memory.intelligence import FailureIntelligenceEngine
from memory.models import MemoryRecord
from memory.retriever import MemoryRetriever


class FailureCategory(str, Enum):
    ASSERTION_ERROR = "ASSERTION_ERROR"
    TIMEOUT_ERROR = "TIMEOUT_ERROR"


@dataclass
class FakeAnalysis:
    test_name: str = "test_something"
    category: FailureCategory = FailureCategory.ASSERTION_ERROR
    root_cause: str = "expected 200 got 404"
    suggested_fix: str = "check endpoint"
    raw_message: str = "AssertionError: expected 200 got 404"
    llm_diagnosis: dict = field(default_factory=dict)


def _record(fix_outcome: str = "pending", days_old: int = 0) -> MemoryRecord:
    ts = datetime.now(tz=timezone.utc).isoformat()
    return MemoryRecord(
        id=uuid.uuid4().hex,
        test_id="test_something",
        endpoint="/api/users",
        method="GET",
        category="ASSERTION_ERROR",
        error_signature="AssertionError expected 200 got 404",
        root_cause="endpoint not found",
        fix_strategy="verify base url",
        fix_outcome=fix_outcome,
        environment="qa",
        run_id="r",
        timestamp=ts,
        ttl_days=90,
    )


def _config(llm_enabled: bool = False, top_k: int = 3) -> MemoryConfig:
    return MemoryConfig(enabled=True, llm_enabled=llm_enabled, similarity_top_k=top_k)


def _make_store(records: list[MemoryRecord]) -> MagicMock:
    store = MagicMock()
    store.query.return_value = records
    return store


def _engine(
    records: list[MemoryRecord], llm=None, llm_enabled: bool = False
) -> FailureIntelligenceEngine:
    cfg = _config(llm_enabled=llm_enabled)
    store = _make_store(records)
    retriever = MemoryRetriever(top_k=3)
    return FailureIntelligenceEngine(store, retriever, cfg, llm)


# ------------------------------------------------------------------
# analyze() with no history
# ------------------------------------------------------------------


def test_analyze_no_history_returns_low_confidence():
    engine = _engine([])
    insight = engine.analyze(FakeAnalysis())
    assert insight.confidence == 0.0


def test_analyze_no_history_no_llm_call():
    llm = MagicMock()
    engine = _engine([], llm=llm, llm_enabled=True)
    engine.analyze(FakeAnalysis())
    llm.complete.assert_not_called()


def test_analyze_no_history_uses_fallback_fix():
    engine = _engine([])
    fa = FakeAnalysis(suggested_fix="my fallback fix")
    insight = engine.analyze(fa)
    assert insight.suggested_fix == "my fallback fix"


def test_analyze_no_history_summary_says_none_found():
    engine = _engine([])
    insight = engine.analyze(FakeAnalysis())
    assert "No similar" in insight.pattern_summary


# ------------------------------------------------------------------
# analyze() with history
# ------------------------------------------------------------------


def test_analyze_with_history_increases_confidence():
    engine = _engine([_record("resolved"), _record("resolved")])
    insight = engine.analyze(FakeAnalysis())
    assert insight.confidence > 0.0


def test_analyze_resolved_records_preferred_for_fix():
    engine = _engine([_record("resolved")])
    insight = engine.analyze(FakeAnalysis())
    assert insight.suggested_fix == "verify base url"


def test_analyze_similar_records_returned():
    engine = _engine([_record()])
    insight = engine.analyze(FakeAnalysis())
    assert len(insight.similar_records) >= 1


def test_analyze_summary_contains_count():
    engine = _engine([_record(), _record()])
    insight = engine.analyze(FakeAnalysis())
    assert "2" in insight.pattern_summary or "similar" in insight.pattern_summary


# ------------------------------------------------------------------
# LLM integration
# ------------------------------------------------------------------


def test_analyze_calls_llm_when_records_present_and_enabled():
    llm = MagicMock()
    llm.complete.return_value = "some suggestion"
    engine = _engine([_record()], llm=llm, llm_enabled=True)
    insight = engine.analyze(FakeAnalysis())
    llm.complete.assert_called_once()
    assert insight.llm_analysis == "some suggestion"


def test_analyze_skips_llm_when_disabled():
    llm = MagicMock()
    engine = _engine([_record()], llm=llm, llm_enabled=False)
    engine.analyze(FakeAnalysis())
    llm.complete.assert_not_called()


def test_analyze_handles_llm_error_gracefully():
    llm = MagicMock()
    llm.complete.return_value = "Claude API error: timeout"
    engine = _engine([_record()], llm=llm, llm_enabled=True)
    insight = engine.analyze(FakeAnalysis())
    assert insight.llm_analysis == ""


# ------------------------------------------------------------------
# _build_prompt
# ------------------------------------------------------------------


def test_build_prompt_includes_test_name():
    engine = _engine([])
    fa = FakeAnalysis(test_name="my_special_test")
    prompt = engine._build_prompt(fa, [])
    assert "my_special_test" in prompt


def test_build_prompt_includes_category():
    engine = _engine([])
    fa = FakeAnalysis()
    prompt = engine._build_prompt(fa, [])
    assert "ASSERTION_ERROR" in prompt


def test_build_prompt_includes_historical_records():
    engine = _engine([])
    rec = _record()
    prompt = engine._build_prompt(FakeAnalysis(), [rec])
    assert "verify base url" in prompt


def test_build_prompt_no_history_block():
    engine = _engine([])
    prompt = engine._build_prompt(FakeAnalysis(), [])
    assert "No historical data" in prompt


# ------------------------------------------------------------------
# _compute_confidence
# ------------------------------------------------------------------


def test_confidence_zero_with_no_records():
    assert FailureIntelligenceEngine._compute_confidence([]) == 0.0


def test_confidence_increases_with_resolved_records():
    records = [_record("resolved"), _record("resolved")]
    conf = FailureIntelligenceEngine._compute_confidence(records)
    assert conf > 0.3


def test_confidence_capped_at_one():
    records = [_record("resolved")] * 20
    conf = FailureIntelligenceEngine._compute_confidence(records)
    assert conf <= 1.0
