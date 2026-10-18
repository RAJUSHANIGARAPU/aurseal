"""Accessibility scans with axe-core: WCAG tags, impact threshold, known defects per route+rule.

A green scan means "no automatically detectable violations". Automated rules catch only
part of WCAG; keyboard flow, focus order and the meaning of alt text still need a human.
"""

from checks.a11y.axe import A11yReport, AxeUnavailable, Violation, scan
from checks.a11y.judge import judge
from checks.a11y.policy import A11yPolicy

__all__ = ["A11yPolicy", "A11yReport", "AxeUnavailable", "Violation", "judge", "scan"]
