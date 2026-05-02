"""Tests for DiscoveryConfig."""

from web_discovery.config import DiscoveryConfig


def test_defaults(monkeypatch):
    monkeypatch.delenv("WD_TARGET_URL", raising=False)
    monkeypatch.delenv("WD_MAX_DEPTH", raising=False)
    monkeypatch.delenv("WD_MAX_PAGES", raising=False)
    cfg = DiscoveryConfig.from_env()
    assert cfg.max_depth == 3
    assert cfg.max_pages == 50
    assert cfg.headless is True
    assert cfg.respect_robots is True
    assert cfg.auth_support is False


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("WD_TARGET_URL", "http://example.com")
    monkeypatch.setenv("WD_MAX_DEPTH", "5")
    monkeypatch.setenv("WD_MAX_PAGES", "100")
    monkeypatch.setenv("WD_HEADLESS", "false")
    monkeypatch.setenv("WD_AUTH_SUPPORT", "true")
    monkeypatch.setenv("WD_AUTH_USERNAME", "admin")
    monkeypatch.setenv("WD_AUTH_PASSWORD", "secret")
    cfg = DiscoveryConfig.from_env()
    assert cfg.target_url == "http://example.com"
    assert cfg.max_depth == 5
    assert cfg.max_pages == 100
    assert cfg.headless is False
    assert cfg.auth_support is True
    assert cfg.auth_username == "admin"
    assert cfg.auth_password == "secret"


def test_output_dir_default(monkeypatch):
    monkeypatch.delenv("WD_OUTPUT_DIR", raising=False)
    cfg = DiscoveryConfig.from_env()
    assert cfg.output_dir is not None


def test_timeout_defaults(monkeypatch):
    monkeypatch.delenv("WD_PAGE_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("WD_WAIT_AFTER_NAV_MS", raising=False)
    cfg = DiscoveryConfig.from_env()
    assert cfg.page_timeout_ms > 0
    assert cfg.wait_after_nav_ms >= 0
