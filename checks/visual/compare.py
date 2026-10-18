"""Compare two PNG screenshots pixel by pixel, the way pixelmatch does, with Pillow.

A pixel counts as different when its perceptual (YIQ) colour distance from the baseline
pixel exceeds ``pixel_threshold`` (0..1, pixelmatch's default 0.1), so anti-aliasing noise
below that level is ignored. The images differ when the share of different pixels exceeds
``max_diff_ratio``. A size change is its own outcome: nothing pixel-wise can be said.

Pillow is imported lazily; without it ``compare`` raises ``PillowUnavailable``.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

MATCH = "match"
MISMATCH = "mismatch"
SIZE_CHANGED = "size_changed"
MISSING_BASELINE = "missing_baseline"

#: Largest possible YIQ delta (pixelmatch), used to scale ``pixel_threshold``.
_MAX_YIQ_DELTA = 35215.0


class PillowUnavailable(RuntimeError):
    """Pillow is not installed."""


def _image_module():
    try:
        from PIL import Image, ImageChops
    except ImportError as exc:
        raise PillowUnavailable(
            "Pillow is not installed (pip install -r requirements-checks.txt)"
        ) from exc
    return Image, ImageChops


@dataclass
class VisualDiff:
    outcome: str
    width: int = 0
    height: int = 0
    diff_pixels: int = 0
    diff_ratio: float = 0.0
    pixel_threshold: float = 0.1
    max_diff_ratio: float = 0.001
    diff_png: bytes | None = None
    detail: str = ""

    @property
    def matched(self) -> bool:
        return self.outcome == MATCH


def decode(data: bytes | None):
    """A PIL image, or ``None`` for empty or undecodable bytes (a 0-byte baseline is missing)."""
    if not data:
        return None
    Image, _ = _image_module()
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:  # noqa: BLE001 - any decode failure means "no usable image"
        return None
    return image


def _rgb(image):
    """Flatten onto white, like pixelmatch blends alpha, and return RGB."""
    Image, _ = _image_module()
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(background, rgba).convert("RGB")
    return image.convert("RGB")


def _yiq_delta(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    r1, g1, b1 = a
    r2, g2, b2 = b
    y = (r1 - r2) * 0.29889531 + (g1 - g2) * 0.58662247 + (b1 - b2) * 0.11448223
    i = (r1 - r2) * 0.59597799 - (g1 - g2) * 0.27417610 - (b1 - b2) * 0.32180189
    q = (r1 - r2) * 0.21147017 - (g1 - g2) * 0.52261711 + (b1 - b2) * 0.31114694
    return 0.5053 * y * y + 0.299 * i * i + 0.1957 * q * q


def compare(
    baseline: bytes | None,
    actual: bytes,
    *,
    pixel_threshold: float = 0.1,
    max_diff_ratio: float = 0.001,
    make_diff: bool = True,
) -> VisualDiff:
    if not 0 <= pixel_threshold <= 1:
        raise ValueError("pixel_threshold must be within 0..1")
    if not 0 <= max_diff_ratio <= 1:
        raise ValueError("max_diff_ratio must be within 0..1")
    Image, ImageChops = _image_module()
    common = {"pixel_threshold": pixel_threshold, "max_diff_ratio": max_diff_ratio}
    actual_img = decode(actual)
    if actual_img is None:
        raise ValueError("the actual screenshot is empty or not an image")
    actual_rgb = _rgb(actual_img)
    width, height = actual_rgb.size
    base_img = decode(baseline)
    if base_img is None:
        return VisualDiff(
            MISSING_BASELINE, width, height, detail="no baseline: nothing compared", **common
        )
    base_rgb = _rgb(base_img)
    if base_rgb.size != actual_rgb.size:
        return VisualDiff(
            SIZE_CHANGED,
            width,
            height,
            detail=f"dimensions changed: baseline {base_rgb.size}, actual {actual_rgb.size}",
            **common,
        )
    max_delta = _MAX_YIQ_DELTA * pixel_threshold * pixel_threshold
    bbox = ImageChops.difference(base_rgb, actual_rgb).getbbox()
    differing: list[tuple[int, int]] = []
    if bbox is not None:
        left, top, right, bottom = bbox
        crop_w = right - left
        base_px = _pixels(base_rgb.crop(bbox))
        act_px = _pixels(actual_rgb.crop(bbox))
        for index, (p, q) in enumerate(zip(base_px, act_px, strict=True)):
            if p != q and _yiq_delta(p, q) > max_delta:
                differing.append((left + index % crop_w, top + index // crop_w))
    total = width * height
    ratio = len(differing) / total if total else 0.0
    outcome = MISMATCH if ratio > max_diff_ratio else MATCH
    diff_png = _diff_image(actual_rgb, differing) if make_diff and differing else None
    return VisualDiff(
        outcome,
        width,
        height,
        diff_pixels=len(differing),
        diff_ratio=ratio,
        diff_png=diff_png,
        detail=(
            f"{len(differing)} of {total} pixels differ ({ratio:.4%}, limit {max_diff_ratio:.4%})"
        ),
        **common,
    )


def _pixels(image):
    """Pixel tuples in row order (``get_flattened_data`` from Pillow 12.1 on)."""
    flat = getattr(image, "get_flattened_data", None)
    return flat() if flat is not None else image.getdata()


def _diff_image(actual_rgb, differing: list[tuple[int, int]]) -> bytes:
    """The actual image, faded to grey, with every differing pixel painted red."""
    Image, _ = _image_module()
    faded = actual_rgb.convert("L").point(lambda v: 160 + v * 95 // 255).convert("RGB")
    pixels = faded.load()
    for x, y in differing:
        pixels[x, y] = (255, 0, 0)
    out = io.BytesIO()
    faded.save(out, format="PNG")
    return out.getvalue()
