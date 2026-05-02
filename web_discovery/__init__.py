"""Autonomous Web App Discovery & Test Generation Engine.

Opt-in via environment variable:
    ENABLE_WEB_DISCOVERY=true python -m web_discovery.cli --url https://example.com

Or register in conftest.py (already done when this module is installed):
    from web_discovery.config import DiscoveryConfig
    config = DiscoveryConfig.from_env()
    if config.enabled:
        from web_discovery.pytest_plugin import WebDiscoveryPlugin
        pluginmanager.register(WebDiscoveryPlugin.from_config(config), "web-discovery")
"""
