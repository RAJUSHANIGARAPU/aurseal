"""
What the health score means when plugins do not report.

The score gates deploys (``deploy = score >= 70``) and it was computed over the
plugins present in ``results``, not the plugins that were supposed to run. A
plugin missing from ``results`` therefore left the denominator instead of
scoring zero — and ``PluginRegistry._load_file`` removes plugins from a run
silently, logging a warning and moving on, whenever a plugin file raises on
import. One bad dependency was enough.

At the limit every plugin failed to import, ``results`` was empty, every tier
was skipped, and the "no plugins run" branch returned 100 — a perfect score and
a greenlit deploy for a run that measured nothing, which is a better outcome
than an honest run where every plugin fails and scores 0.

So these tests are written from both ends: the broken-run cases, and the
healthy-run cases that prove the score can still reach 100 when it is earned.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from orchestration.master_orchestrator import MasterOrchestrator
from plugins._base_plugin import PluginPriority, PluginResult

CRITICAL = PluginPriority.CRITICAL
HIGH = PluginPriority.HIGH
NORMAL = PluginPriority.NORMAL
BACKGROUND = PluginPriority.BACKGROUND


@dataclass
class FakePlugin:
    name: str
    priority: PluginPriority


def _tiers(**by_tier: list[str]) -> dict[PluginPriority, list[FakePlugin]]:
    """Build the by_priority map the orchestrator passes in, from plugin names."""
    tiers: dict[PluginPriority, list[FakePlugin]] = {p: [] for p in PluginPriority}
    for tier_name, names in by_tier.items():
        priority = PluginPriority[tier_name.upper()]
        tiers[priority] = [FakePlugin(name=n, priority=priority) for n in names]
    return tiers


def _results(**statuses: str) -> dict[str, PluginResult]:
    return {name: PluginResult(status=status) for name, status in statuses.items()}


def _score(results, tiers) -> int:
    """Score without booting an orchestrator.

    ``__init__`` scans the real plugin tree, opens a SQLite file and starts a
    trace log; none of that is involved in scoring.
    """
    orchestrator = object.__new__(MasterOrchestrator)
    return orchestrator._compute_health(results, tiers)


class TestARunThatMeasuredNothingIsNotAPass:
    def test_no_plugin_loaded_does_not_score_100(self):
        """
        Every plugin file failed to import, so nothing reported.

        This is the deploy-gate hole: the run knows nothing about the product
        and used to say so with the highest score it can give.
        """
        tiers = _tiers(critical=["security"], high=["contracts"], normal=["lint"])
        assert _score({}, tiers) == 0

    def test_an_empty_registry_does_not_score_100(self):
        """No plugins matched the trigger at all — still no evidence."""
        assert _score({}, _tiers()) == 0

    def test_a_background_only_run_does_not_score_100(self):
        """
        BACKGROUND plugins are fired as daemon threads and never join.

        They carry weight 0.0 by design, so a run holding nothing else has
        measured nothing, whatever those threads eventually do.
        """
        assert _score({}, _tiers(background=["telemetry"])) == 0

    def test_a_missing_plugin_scores_zero_rather_than_vanishing(self):
        """Two critical plugins, one reported a pass, one never loaded."""
        tiers = _tiers(critical=["security", "secrets"])
        assert _score(_results(security="pass"), tiers) == 50

    def test_a_critical_tier_that_stopped_early_does_not_score_the_survivors(self):
        """
        CRITICAL runs sequentially and breaks on the first failure.

        The plugins after the break never ran, so the run has no opinion on
        them — and no opinion must not read as a pass.
        """
        tiers = _tiers(critical=["a", "b", "c", "d"])
        assert _score(_results(a="pass", b="fail"), tiers) == 25

    def test_below_the_deploy_threshold(self):
        """The whole point: a run that measured nothing must not clear 70."""
        tiers = _tiers(critical=["security"], high=["contracts"])
        assert _score({}, tiers) < 70


class TestAHealthyRunStillScoresHealthy:
    """
    Positive controls.

    Every test above asserts that some run scores low. If the scoring function
    were replaced with ``return 0`` they would all still pass, so these fix the
    other end: the mechanism has to be able to report a good run as good.
    """

    def test_everything_passing_scores_100(self):
        tiers = _tiers(critical=["security"], high=["contracts"], normal=["lint"])
        results = _results(security="pass", contracts="pass", lint="pass")
        assert _score(results, tiers) == 100

    def test_everything_passing_clears_the_deploy_threshold(self):
        tiers = _tiers(critical=["security"], high=["contracts"])
        assert _score(_results(security="pass", contracts="pass"), tiers) >= 70

    def test_everything_failing_scores_0(self):
        tiers = _tiers(critical=["security"], high=["contracts"], normal=["lint"])
        results = _results(security="fail", contracts="fail", lint="error")
        assert _score(results, tiers) == 0

    @pytest.mark.parametrize("status", ["pass", "warn"])
    def test_a_verdict_of_health_counts_as_passing(self, status):
        """Both mean the plugin looked and formed an opinion."""
        tiers = _tiers(critical=["security"])
        assert _score(_results(security=status), tiers) == 100

    @pytest.mark.parametrize("status", ["fail", "error", "unknown"])
    def test_anything_short_of_a_verdict_does_not(self, status):
        """
        `unknown` is new and is the point of it: a plugin that ran but could not
        tell — an outage, an empty input — must not score as one that verified.
        """
        tiers = _tiers(critical=["security"])
        assert _score(_results(security=status), tiers) == 0

    def test_tier_weights_still_apply(self):
        """CRITICAL 40 / HIGH 35 / NORMAL 25 — losing CRITICAL costs 40 points."""
        tiers = _tiers(critical=["security"], high=["contracts"], normal=["lint"])
        results = _results(security="fail", contracts="pass", lint="pass")
        assert _score(results, tiers) == 60

    def test_a_tier_with_no_plugins_is_not_scored_against(self):
        """A run with only NORMAL plugins, all green, is still a 100."""
        tiers = _tiers(normal=["lint", "format"])
        assert _score(_results(lint="pass", format="pass"), tiers) == 100

    def test_background_plugins_do_not_drag_a_good_score_down(self):
        """Weight 0.0 — they must not be able to fail a run they never joined."""
        tiers = _tiers(critical=["security"], background=["telemetry"])
        assert _score(_results(security="pass"), tiers) == 100


class TestSkipLeavesTheFractionRatherThanFillingIt:
    """
    `skip` used to count as passing, so a tier whose only plugin skipped scored
    full marks. `e2e_playwright` returns `skip` when its test directory is
    missing, and on a `ui_change` trigger it is the entire HIGH tier — a wrong
    working directory bought 35 points having run no browser test.

    It is now scored neither way: excluded from numerator and denominator both.
    "I could not run" is `unknown`; `skip` means nobody expected a verdict.
    """

    def test_a_lone_skip_contributes_no_weight(self):
        tiers = _tiers(critical=["security"], normal=["lint"])
        # CRITICAL skips entirely; the score is NORMAL's alone.
        assert _score(_results(security="skip", lint="fail"), tiers) == 0

    def test_a_skip_does_not_dilute_a_failure(self):
        """Two plugins, one skipped and one failed, is 0 — not 50."""
        tiers = _tiers(critical=["a", "b"])
        assert _score(_results(a="skip", b="fail"), tiers) == 0

    def test_a_skip_does_not_dilute_a_pass_either(self):
        """The control: skipping must not cost points it never owed."""
        tiers = _tiers(critical=["a", "b"])
        assert _score(_results(a="skip", b="pass"), tiers) == 100

    def test_a_run_where_everything_skipped_is_not_a_pass(self):
        """No plugin was applicable, so the run holds no evidence at all."""
        tiers = _tiers(critical=["security"], high=["contracts"])
        assert _score(_results(security="skip", contracts="skip"), tiers) == 0

    def test_an_unknown_still_sits_in_the_denominator(self):
        """
        The distinction that matters: `skip` leaves, `unknown` stays and counts
        against. One plugin passing and one unable to tell is half marks.
        """
        tiers = _tiers(critical=["a", "b"])
        assert _score(_results(a="pass", b="unknown"), tiers) == 50
