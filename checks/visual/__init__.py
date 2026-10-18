"""Visual regression: committed baselines per browser and platform, a pixelmatch-style diff
with Pillow, evidence images, and a human approval step."""

from checks.visual.baselines import BaselineStore
from checks.visual.compare import PillowUnavailable, VisualDiff, compare

__all__ = ["BaselineStore", "PillowUnavailable", "VisualDiff", "compare"]
