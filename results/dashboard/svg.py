"""Hand-rolled inline SVG charts: deterministic text, no script, colours from CSS classes.

Colours are not baked in: every mark carries a class (``o-passed``, ``o-failed``,
``series``...) that the page stylesheet colours for light and dark themes. Every point
carries a ``<title>`` with its denominator, so a hover never shows a bare percentage.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from html import escape

WIDTH = 640
HEIGHT = 180
PAD_LEFT = 44
PAD_RIGHT = 12
PAD_TOP = 12
PAD_BOTTOM = 28


def _fmt(value: float) -> str:
    text = f"{value:.1f}"
    return text[:-2] if text.endswith(".0") else text


def line_chart(
    points: Sequence[tuple[str, float | None]],
    *,
    y_label: str,
    denominators: Sequence[str],
    y_max: float = 100.0,
    unit: str = "%",
) -> str:
    """A line over ``points`` (label, value); ``None`` is a gap with a hollow marker.

    ``denominators[i]`` is shown in the tooltip of point ``i`` (e.g. "46/50 executed").
    """
    n = len(points)
    plot_w = WIDTH - PAD_LEFT - PAD_RIGHT
    plot_h = HEIGHT - PAD_TOP - PAD_BOTTOM
    top = y_max if y_max > 0 else 1.0

    def x(i: int) -> float:
        return PAD_LEFT + (plot_w * (i + 0.5) / n if n else 0)

    def y(v: float) -> float:
        return PAD_TOP + plot_h * (1 - min(max(v, 0.0), top) / top)

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
        f'class="chart" role="img" aria-label="{escape(y_label)}">'
    ]
    for frac in (0.0, 0.5, 1.0):
        gy = _fmt(y(top * frac))
        out.append(
            f'<line class="grid" x1="{PAD_LEFT}" y1="{gy}" x2="{WIDTH - PAD_RIGHT}" y2="{gy}"/>'
        )
        out.append(
            f'<text class="axis" x="{PAD_LEFT - 6}" y="{gy}" text-anchor="end" '
            f'dominant-baseline="middle">{_fmt(top * frac)}{escape(unit)}</text>'
        )
    out.append(f'<text class="axis" x="{PAD_LEFT}" y="{HEIGHT - 6}">{escape(y_label)}</text>')
    segment: list[str] = []
    segments: list[list[str]] = []
    for i, (_, value) in enumerate(points):
        if value is None:
            if segment:
                segments.append(segment)
            segment = []
            continue
        segment.append(f"{_fmt(x(i))},{_fmt(y(value))}")
    if segment:
        segments.append(segment)
    for seg in segments:
        if len(seg) > 1:
            out.append(f'<polyline class="series" fill="none" points="{" ".join(seg)}"/>')
    for i, (label, value) in enumerate(points):
        den = denominators[i] if i < len(denominators) else ""
        if value is None:
            tip = f"{label}: unmeasured" + (f" ({den})" if den else "")
            out.append(
                f'<circle class="unmeasured" cx="{_fmt(x(i))}" cy="{_fmt(y(0))}" r="4">'
                f"<title>{escape(tip)}</title></circle>"
            )
            continue
        tip = f"{label}: {_fmt(value)}{unit}" + (f" ({den})" if den else "")
        out.append(
            f'<circle class="point" cx="{_fmt(x(i))}" cy="{_fmt(y(value))}" r="3.5">'
            f"<title>{escape(tip)}</title></circle>"
        )
    out.append("</svg>")
    return "".join(out)


def timeline(
    outcomes: Sequence[tuple[str, str]],
    boundaries: Collection[str] = (),
    *,
    cell: int = 14,
) -> str:
    """One square per run, coloured by outcome; a dashed rule before each revision change."""
    width = max(len(outcomes) * (cell + 2) + 2, cell + 4)
    height = cell + 12
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'class="timeline" role="img" aria-label="outcome per run">'
    ]
    for i, (run_id, outcome) in enumerate(outcomes):
        x0 = 2 + i * (cell + 2)
        if run_id in boundaries:
            out.append(
                f'<line class="boundary" x1="{x0 - 1}" y1="0" x2="{x0 - 1}" y2="{height}">'
                f"<title>revision changed at {escape(run_id)}</title></line>"
            )
        out.append(
            f'<rect class="o-{escape(outcome)}" x="{x0}" y="6" width="{cell}" height="{cell}" '
            f'rx="2"><title>{escape(run_id)}: {escape(outcome)}</title></rect>'
        )
    out.append("</svg>")
    return "".join(out)


__all__ = ["line_chart", "timeline"]
