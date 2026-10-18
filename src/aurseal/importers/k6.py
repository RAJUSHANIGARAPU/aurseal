"""k6 importer: the end-of-test summary JSON, and the ``perf.k6`` result type.

Two inputs are read:

* ``handleSummary`` data written with ``JSON.stringify(data)``: ``metrics`` maps a metric
  (or sub-metric, ``http_req_duration{status:200}``) to ``type``, ``contains``,
  ``values`` and ``thresholds`` (``{"p(95)<500": {"ok": false}}``); see the k6 custom
  summary documentation.
* ``k6 run --summary-export``: the same data with each metric flattened to its values,
  thresholds as booleans that are true when the threshold *failed* (k6's
  ``oldJSONSummary`` writes ``!threshold.ok``), and a rate's ``rate`` renamed ``value``.

k6's newer machine-readable summary (``--new-machine-readable-summary``, schema
``k6-summary`` 1.0.0) carries no threshold outcomes, so it has no verdict to import and is
refused with a message saying so.

Every threshold is one record: suite = the metric, name = the threshold expression,
status passed or failed. The summary does not name the script, so the path is empty:
keep one script's summaries in their own project (or run) to keep identities apart. Each
record carries a :class:`K6Payload` (result type ``perf.k6``, schema ``perf.k6/1``): the
metric's type and values, the threshold, its outcome and the observed value. A run
without thresholds has no pass/fail verdict and yields no records.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import IO, Any, Final, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from aurseal.importers._build import PendingTest, ReportError
from aurseal.model.v1 import ResultTypePayload, Status, TestRecord

FRAMEWORK = "k6"
RESULT_TYPE = "perf.k6"
SCHEMA_REF: Final = "perf.k6/1"
_AGGREGATE_RE = re.compile(r"^\s*([a-z]+(?:\(\s*\d+(?:\.\d+)?\s*\))?)\s*(?:<=|>=|===|==|!=|<|>)")
MetricType = Literal["counter", "gauge", "rate", "trend"]


class K6Payload(BaseModel):
    """The ``perf.k6/1`` payload: one threshold and the metric it judges."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_ref: Literal["perf.k6/1"] = SCHEMA_REF
    source: Literal["handle_summary", "summary_export"]
    metric: str
    metric_type: MetricType | None = None
    contains: str | None = None
    values: dict[str, float]
    threshold: str
    ok: bool
    observed: float | None = Field(
        default=None, description="The value the threshold's aggregation judged, if known."
    )
    test_run_duration_ms: float | None = None


class K6ResultType:
    """The ``perf.k6`` result type: k6 threshold outcomes with their metric values."""

    name = RESULT_TYPE
    schema_ref = SCHEMA_REF

    def payload_schema(self) -> dict[str, Any]:
        schema = K6Payload.model_json_schema()
        schema["$id"] = f"https://aurseal.invalid/schema/{SCHEMA_REF}.json"
        return schema

    def validate(self, payload: dict[str, Any]) -> K6Payload:
        return K6Payload.model_validate(payload)


def _numbers(values: dict[str, Any]) -> dict[str, float]:
    return {
        str(k): float(v)
        for k, v in values.items()
        if isinstance(v, int | float) and not isinstance(v, bool)
    }


def _infer_type(values: dict[str, float]) -> MetricType | None:
    """The metric type from the shape of a flattened ``--summary-export`` metric."""
    if "passes" in values and "fails" in values:
        return "rate"
    if "count" in values:
        return "counter"
    if "avg" in values or "med" in values:
        return "trend"
    if "value" in values:
        return "gauge"
    return None


def _metric(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, bool], bool]:
    """``(fields, {threshold: ok}, legacy)`` of one metric in either input."""
    if isinstance(raw.get("values"), dict):  # handleSummary data
        thresholds = {
            str(name): bool(t.get("ok"))
            for name, t in (raw.get("thresholds") or {}).items()
            if isinstance(t, dict) and isinstance(t.get("ok"), bool)
        }
        values = _numbers(raw["values"])
        metric_type = raw.get("type")
        fields = {
            "metric_type": metric_type if metric_type in get_args(MetricType) else None,
            "contains": str(raw["contains"]) if raw.get("contains") else None,
            "values": values,
        }
        return fields, thresholds, False
    thresholds = {
        str(name): not failed
        for name, failed in (raw.get("thresholds") or {}).items()
        if isinstance(failed, bool)
    }
    values = _numbers(raw)
    inferred = _infer_type(values)
    if inferred == "rate" and "value" in values:  # undo the export's rename
        values["rate"] = values.pop("value")
    return {"metric_type": inferred, "contains": None, "values": values}, thresholds, True


def _observed(threshold: str, values: dict[str, float]) -> float | None:
    match = _AGGREGATE_RE.match(threshold)
    if not match:
        return None
    return values.get(re.sub(r"\s+", "", match.group(1)))


class K6Importer:
    name = "k6"

    def sniff(self, head: bytes) -> float:
        text = head.lstrip()
        if not text.startswith(b"{"):
            return 0.0
        if b'"k6Version"' in text:
            return 0.9  # machine-readable summary: parse() explains why it is refused
        if b'"root_group"' in text and b'"metrics"' in text:
            return 0.9
        if b'"root_group"' in text:
            return 0.8
        if b'"metrics"' in text and (b'"http_req_duration"' in text or b'"iterations"' in text):
            return 0.7
        return 0.0

    def parse(self, stream: IO[bytes]) -> Iterator[TestRecord]:
        try:
            document = json.load(stream)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ReportError(f"not a k6 summary: {exc}") from exc
        if not isinstance(document, dict):
            raise ReportError("not a k6 summary: not a JSON object")
        if "k6Version" in (document.get("metadata") or {}):
            raise ReportError(
                "the k6 machine-readable summary has no threshold outcomes; export the "
                "handleSummary data (JSON.stringify(data)) or use --summary-export"
            )
        metrics = document.get("metrics")
        if not isinstance(metrics, dict):
            raise ReportError("not a k6 summary: no 'metrics' object")
        state = document.get("state") or {}
        duration = state.get("testRunDurationMs")
        run_ms = float(duration) if isinstance(duration, int | float) else None
        for metric_name, raw in metrics.items():
            if not isinstance(raw, dict):
                continue
            fields, thresholds, legacy = _metric(raw)
            for threshold, ok in thresholds.items():
                observed = _observed(threshold, fields["values"])
                payload = K6Payload(
                    source="summary_export" if legacy else "handle_summary",
                    metric=str(metric_name),
                    threshold=threshold,
                    ok=ok,
                    observed=observed,
                    test_run_duration_ms=run_ms,
                    **fields,
                )
                yield self._record(str(metric_name), threshold, payload)

    @staticmethod
    def _record(metric: str, threshold: str, payload: K6Payload) -> TestRecord:
        pending = PendingTest(framework=FRAMEWORK, path="", suite=[metric], title=threshold)
        message = None
        if not payload.ok:
            seen = f" (observed {payload.observed:g})" if payload.observed is not None else ""
            message = f"threshold {threshold} on {metric} crossed{seen}"
        pending.add(Status.PASSED if payload.ok else Status.FAILED, message=message)
        record = pending.record()
        result_type = ResultTypePayload(type=RESULT_TYPE, payload=payload.model_dump(mode="json"))
        return TestRecord.model_validate(
            {**record.model_dump(), "result_type": result_type.model_dump()}
        )
