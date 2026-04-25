import logging
import os
import shutil
from datetime import datetime

import pytest

from core.config_reader import ConfigReader
from core.failure_reporter import write_failure_bundle

# =========================================================
# CLI OPTIONS (ONLY ENV — let Playwright handle browser)
# =========================================================


def pytest_addoption(parser):
    parser.addoption("--env", action="store", default="qa")


# =========================================================
# FLAKINESS TRACKING
# =========================================================


def pytest_configure(config):
    from autonomous_ui.flakiness.pytest_plugin import FlakinessPlugin

    if not config.pluginmanager.hasplugin("flakiness-tracker"):
        config.pluginmanager.register(FlakinessPlugin.from_config(config), "flakiness-tracker")

    # Memory Intelligence Layer — opt-in via ENABLE_MEMORY=true
    from memory.config import MemoryConfig

    mem_config = MemoryConfig.from_env()
    if mem_config.enabled and not config.pluginmanager.hasplugin("memory-tracker"):
        from memory.pytest_plugin import MemoryPlugin

        config.pluginmanager.register(MemoryPlugin.from_config(mem_config), "memory-tracker")

    # Contract Testing Layer — opt-in via ENABLE_CONTRACT_TESTING=true
    from contract_testing.config import ContractConfig

    ct_config = ContractConfig.from_env()
    if ct_config.enabled and not config.pluginmanager.hasplugin("contract-testing"):
        from contract_testing.pytest_plugin import ContractPlugin

        config.pluginmanager.register(ContractPlugin.from_config(ct_config), "contract-testing")


# =========================================================
# GLOBAL LOGGING SETUP
# =========================================================


@pytest.fixture(scope="session", autouse=True)
def setup_logging():
    os.makedirs("reports/logs", exist_ok=True)
    logger = logging.getLogger("wiki_project")
    logger.setLevel(logging.INFO)

    if logger.hasHandlers():
        logger.handlers.clear()

    file_handler = logging.FileHandler("reports/logs/test.log")
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.propagate = False
    logger.info("LOGGER INITIALIZED")


# =========================================================
# ENV CONFIG FIXTURE
# =========================================================


@pytest.fixture(scope="session")
def config(request):
    env = request.config.getoption("env")
    return ConfigReader(env)


# =========================================================
# PER-TEST EVIDENCE COLLECTION (console errors + failed requests)
# =========================================================

_console_errors: list[str] = []
_failed_requests: list[str] = []


@pytest.fixture(autouse=True)
def collect_browser_evidence(page, request):
    """Accumulates console errors and failed network requests per test."""
    _console_errors.clear()
    _failed_requests.clear()

    def on_console(msg):
        if msg.type == "error":
            _console_errors.append(msg.text)

    def on_response(response):
        if response.status >= 400:
            _failed_requests.append(f"{response.request.method} {response.url} → {response.status}")

    page.on("console", on_console)
    page.on("response", on_response)
    yield


# =========================================================
# ENABLE VIDEO + TRACING USING PLUGIN CONTEXT
# =========================================================


@pytest.fixture(autouse=True)
def enable_artifacts(context, request):
    os.makedirs("reports/videos", exist_ok=True)
    os.makedirs("reports/traces", exist_ok=True)
    context.tracing.start(screenshots=True, snapshots=True, sources=True)
    yield
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    test_name = request.node.name
    browser = request.config.getoption("--browser")

    trace_path = f"reports/traces/{test_name}_{browser}_{timestamp}.zip"
    context.tracing.stop(path=trace_path)

    videos = []
    for page in context.pages:
        if page.video:
            videos.append(page.video)

    context.close()

    for video in videos:
        video_path = video.path()
        new_video = f"reports/videos/{test_name}_{browser}_{timestamp}.webm"
        shutil.move(video_path, new_video)


# =========================================================
# VIDEO RECORDING CONFIG (PLUGIN WAY)
# =========================================================


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {**browser_context_args, "record_video_dir": "reports/videos/"}


# =========================================================
# SCREENSHOT ON FAILURE
# =========================================================


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()

    if rep.when == "call" and rep.failed:
        page = item.funcargs.get("page")
        screenshot_bytes = None
        dom_snapshot = ""

        if page:
            os.makedirs("reports/screenshots", exist_ok=True)
            screenshot_path = f"reports/screenshots/{item.name}.png"
            try:
                page.screenshot(path=screenshot_path, full_page=True)
                screenshot_bytes = open(screenshot_path, "rb").read()
            except Exception:
                pass
            try:
                dom_snapshot = page.content()
            except Exception:
                pass

        write_failure_bundle(
            test_name=item.name,
            error=call.excinfo.value if call.excinfo else Exception("unknown"),
            screenshot_bytes=screenshot_bytes,
            console_errors=list(_console_errors),
            failed_requests=list(_failed_requests),
            dom_snapshot=dom_snapshot,
        )
