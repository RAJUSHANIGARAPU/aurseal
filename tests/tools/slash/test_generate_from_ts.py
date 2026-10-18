"""generate-from-ts: cleaning a codegen recording and linting the code made from it."""

from __future__ import annotations

import json

from tests.tools.slash._commands import claude_md_lists, read_command
from tools.slash import recording as rec

RECORDING = """import { test, expect } from '@playwright/test';

test('test', async ({ page }) => {
  await page.goto('https://shop.example.test/login');
  await page.getByLabel('Username').fill('kim');
  await page.getByLabel('Password').fill('pw-123');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.getByRole('button', { name: 'Accept all cookies' }).click();
  await page.goto('https://shop.example.test/checkout');
  await page.getByPlaceholder('Postal code').click();
  await page.getByPlaceholder('Postal code').fill('1011');
  await page.getByPlaceholder('Postal code').press('ArrowLeft');
  await page.getByPlaceholder('Postal code').press('Backspace');
  await page.getByPlaceholder('Postal code').fill('1012 AB');
  await page.locator('.cdk-overlay-backdrop').click();
  await page.locator('[data-testid="delivery-date"]').fill('2026-11-02');
  await page.locator('#mat-input-12').fill('Leave at the door');
  await page.getByRole('button', { name: 'Place order' }).click();
  await page.getByRole('button', { name: 'Place order' }).click();
  await expect(page.getByTestId('order-status')).toHaveText('Order placed');
});
"""


def test_sign_in_and_consent_steps_are_dropped():
    result = rec.clean(RECORDING)
    raw = " ".join(s.raw for s in result.steps)
    for gone in ("Username", "Password", "Sign in", "cookies", "/login", "pw-123"):
        assert gone not in raw, gone
    assert rec.flow_name(RECORDING) == "checkout"


def test_noise_collapses_and_steps_group_by_intent():
    result = rec.clean(RECORDING)
    fills = [s for s in result.steps if s.action == "fill"]
    assert [s.value for s in fills] == ["1012 AB", "2026-11-02", "Leave at the door"]
    assert sum(1 for s in result.steps if s.action == "click") == 1  # repeated click kept once
    assert any(s.action == "wait_hidden" for s in result.steps)  # overlay click became a wait
    assert list(result.by_intent()) == ["navigate", "fill", "submit", "verify"]
    assert result.needs_semantic == ["#mat-input-12"]


def test_locators_follow_the_preference_order():
    locators = rec.build_locators(rec.clean(RECORDING))
    assert {"type": "testid", "value": "delivery-date"} in locators.values()
    assert {"type": "placeholder", "value": "Postal code"} in locators.values()
    assert {"type": "role", "role": "button", "name": "Place order"} in locators.values()
    assert rec.to_entry("locator", '[aria-label="Close"]') == {
        "type": "aria_label",
        "value": "Close",
    }
    css = [v for v in locators.values() if v["type"] == "css"]
    assert all("data-testid" not in v["value"] for v in css)
    assert rec.lint_locators(locators) == []


def test_every_literal_goes_to_the_data_file():
    result = rec.clean(RECORDING)
    data = rec.build_data(result, rec.build_locators(result))
    assert set(data.values()) >= {
        "1012 AB",
        "2026-11-02",
        "Leave at the door",
        "Order placed",
        "/checkout",
    }


GOOD_PAGE = """
from core.base_page import BasePage


class CheckoutPage(BasePage):
    def __init__(self, page, config):
        super().__init__(page, config, "ui/locators/checkout_locators.json")

    def enter_postal_code(self, value):
        self.resolve("postal_code_field").fill(value)
        return self
"""

GOOD_TEST = """
import json

import pytest

from ui.pages.checkout_page import CheckoutPage


@pytest.mark.smoke
def test_place_order(page, config):
    data = json.loads(open("ui/testdata/checkout.json").read())
    CheckoutPage(page, config).enter_postal_code(data["postal_code_field_value"])
"""


def test_clean_generated_code_passes_the_lint():
    data = {"postal_code_field_value": "1012 AB"}
    assert rec.lint(GOOD_PAGE, GOOD_TEST, data) == []


def test_raw_selectors_sleeps_direct_calls_and_literals_are_flagged():
    page = GOOD_PAGE.replace('self.resolve("postal_code_field")', 'self.page.locator("#zip")')
    page = page.replace("from core.base_page import BasePage", "import time\nBasePage = object")
    test = GOOD_TEST + '\n    page.wait_for_timeout(500)\n    assert "1012 AB"\n'
    problems = rec.lint(
        page.replace("class CheckoutPage(BasePage)", "class CheckoutPage"), test, {"k": "1012 AB"}
    )
    text = "\n".join(problems)
    assert "raw selector" in text and "sleep" in text and "calls Playwright directly" in text
    assert "belongs in the test-data file" in text and "does not extend BasePage" in text


def test_existing_files_are_extended_not_overwritten():
    existing = {"save_button": {"type": "testid", "value": "save"}}
    new = {
        "save_button": {"type": "css", "value": ".x"},
        "cancel_button": {"type": "text", "value": "Cancel"},
    }
    merged = rec.merge(existing, new)
    assert merged["save_button"] == existing["save_button"] and "cancel_button" in merged


def test_the_write_mode_merges_into_existing_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ui" / "locators").mkdir(parents=True)
    (tmp_path / "ui" / "locators" / "checkout_locators.json").write_text(
        json.dumps({"kept": {"type": "testid", "value": "k"}}), encoding="utf-8"
    )
    src = tmp_path / "r.spec.ts"
    src.write_text(RECORDING, encoding="utf-8")
    assert rec.main(["clean", str(src), "--write"]) == 0
    locators = json.loads((tmp_path / "ui/locators/checkout_locators.json").read_text())
    assert "kept" in locators and len(locators) > 1
    assert (tmp_path / "ui/testdata/checkout.json").exists()


def test_the_command_is_manual_only_and_commits_only_after_three_passes():
    meta, body = read_command("generate-from-ts")
    body = " ".join(body.split())
    assert meta["disable-model-invocation"] is True
    assert claude_md_lists("generate-from-ts")
    for promise in (
        "3 consecutive passes",
        "at most 5 fix rounds",
        "commit nothing",
        "never overwrite",
        "tools.slash.recording lint",
        "BasePage",
    ):
        assert promise in body, promise
