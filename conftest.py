import logging
import os
from datetime import datetime

import pytest

from core import artifact_names
from core.artifact_policy import (
    RetentionPolicy,
    finalize_videos,
    finish_trace,
    page_videos,
    worth_keeping,
)
from core.browser_evidence import BrowserEvidence
from core.config_reader import ConfigReader
from core.failure_reporter import write_failure_bundle

# =========================================================
# CLI OPTIONS (ONLY ENV — let Playwright handle browser)
# =========================================================


def pytest_addoption(parser):
    parser.addoption(
        "--env",
        action="store",
        default=None,
        help="local | qa | staging (default: $TEST_ENV or qa)",
    )
    parser.addoption(
        "--supplied-token",
        action="store",
        default=None,
        help="bearer JWT for AUTH_MODE=token (prefer the SUPPLIED_TOKEN env var)",
    )
    parser.addoption(
        "--release-line",
        action="store",
        default=None,
        help="deselect known_defect tests whose fix_line is another release line "
        "(default: $RELEASE_LINE)",
    )


# =========================================================
# FLAKINESS TRACKING
# =========================================================


# Suite-wide test-data fixtures (per-test cleanup ledger, session seed configuration) and
# test plans (`pytest --plan plans/<name>.yaml`; its option must exist at startup).
pytest_plugins = ["test_data.pytest_plugin", "traceability.plan_plugin"]


def pytest_configure(config):
    # Top rung of the auth settings ladder; the env var is preferred (CI logs echo command lines).
    token = config.getoption("--supplied-token", default=None)
    if token:
        os.environ["SUPPLIED_TOKEN"] = token

    from autonomous_ui.flakiness.pytest_plugin import FlakinessPlugin

    if not config.pluginmanager.hasplugin("flakiness-tracker"):
        config.pluginmanager.register(FlakinessPlugin.from_config(config), "flakiness-tracker")

    # Expected-red tests for open defects: @pytest.mark.known_defect("ID", raises=...)
    from verification.pytest_plugin import register as register_known_defects

    register_known_defects(config)

    # Run gate: run id, circuit-breaker abstention, journey-group fail-fast, lane markers,
    # and the run-trust verdict when RUN_TRUST=fail|unstable|report.
    from gate.pytest_plugin import register as register_run_gate

    register_run_gate(config)

    # Results store: controller-only recorder (runs, attempts, evidence, verdicts) into
    # reports/results/results.db; RESULTS_STORE=off disables it.
    from results.pytest_plugin import register as register_results

    register_results(config)

    # Product requirement links: @pytest.mark.requirement("REQ-n"), checked against
    # requirements/; writes reports/traceability/case-links.json once per run.
    from traceability.pytest_plugin import register as register_requirements

    register_requirements(config)

    # Allure environment.properties / executor.json / categories.json (controller only),
    # one run id pinned for every xdist worker, and per-step screenshots when enabled.
    from reporting.pytest_plugin import register as register_report_metadata

    register_report_metadata(config)

    # Lets fixture teardown ask "did this test fail?" (trace/video retention).
    from core import artifact_policy

    artifact_policy.register(config)

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

    # Web Discovery Layer — opt-in via ENABLE_WEB_DISCOVERY=true
    import os as _os

    if _os.getenv("ENABLE_WEB_DISCOVERY", "false").lower() in ("1", "true", "yes"):
        if not config.pluginmanager.hasplugin("web_discovery_plugin"):
            from web_discovery.pytest_plugin import WebDiscoveryPlugin

            config.pluginmanager.register(WebDiscoveryPlugin(), "web_discovery_plugin")

    # Knowledge Graph Layer — opt-in via ENABLE_GRAPHIFY=true
    graphify_enabled = os.environ.get("ENABLE_GRAPHIFY", "").lower() == "true"
    if graphify_enabled and not config.pluginmanager.hasplugin("graphify-knowledge-graph"):
        from core.graphify.pytest_plugin import GraphifyPlugin

        config.pluginmanager.register(GraphifyPlugin.from_env(), "graphify-knowledge-graph")


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
# PER-TEST EVIDENCE COLLECTION (console errors, page errors, failed requests)
# =========================================================

_EVIDENCE = pytest.StashKey[BrowserEvidence]()


@pytest.fixture(autouse=True)
def collect_browser_evidence(context, page, request):
    """Console errors, page errors and failed requests of every page in the test's context."""
    request.node.stash[_EVIDENCE] = BrowserEvidence().attach_context(context)
    from reporting.step_screenshots import set_current_page

    set_current_page(page)
    yield
    set_current_page(None)


# =========================================================
# GATEWAY 5xx RETRY — opt-in here (HTTP_RETRY_5XX=<n>): the browser lane targets a
# third-party site, and routing every read through a fetch would serialise its traffic.
# =========================================================


@pytest.fixture(autouse=True)
def gateway_retry(context):
    if os.environ.get("HTTP_RETRY_5XX"):
        from core import browser_resilience
        from gate.http_retry import Decision, RetryPolicy

        policy = RetryPolicy.from_env()
        browser_resilience.install(
            context,
            protects=policy.protects,
            should_retry=lambda m, u, s: policy.decide(m, u, s, 0) is Decision.RETRY,
            max_retries=policy.max_retries,
            backoff_s=policy.backoff_s,
        )
    yield


# =========================================================
# ENABLE VIDEO + TRACING USING PLUGIN CONTEXT
# =========================================================


@pytest.fixture(autouse=True)
def enable_artifacts(context, request, browser_name):
    os.makedirs(artifact_names.VIDEO_DIR, exist_ok=True)
    os.makedirs(artifact_names.TRACE_DIR, exist_ok=True)
    context.tracing.start(screenshots=True, snapshots=True, sources=True)
    yield
    policy = RetentionPolicy.from_env()  # TRACE_ALWAYS / VIDEO_ALWAYS keep everything
    keep = worth_keeping(request.node)  # failed, or skipped from inside the test
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    test_name = request.node.name

    # `browser_name` is pytest-playwright's own fixture and is a string. The
    # previous `request.config.getoption("--browser")` is declared
    # action="append" with a default of [], so it put a literal "[]" in every
    # filename.
    trace_path = artifact_names.trace_path(test_name, browser_name, timestamp)
    finish_trace(context, policy.keep_trace(keep), trace_path)
    videos = page_videos(context)
    context.close()  # flushes the video files
    finalize_videos(
        videos,
        policy.keep_video(keep),
        [
            artifact_names.video_path(test_name, browser_name, timestamp, i, len(videos))
            for i in range(len(videos))
        ],
    )


# =========================================================
# BROWSER LAUNCH ARGS — stealth mode for external sites
# =========================================================


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args, browser_name):
    # Chromium switches: WebKit refuses to start on an unknown option.
    if browser_name != "chromium":
        return browser_type_launch_args
    from core.chrome_launch import with_chrome_args

    return {
        **browser_type_launch_args,
        "args": with_chrome_args(
            [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ]
        ),
    }


# =========================================================
# VIDEO RECORDING CONFIG (PLUGIN WAY)
# =========================================================


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    from core import run_id

    args = {
        **browser_context_args,
        "record_video_dir": "reports/videos/",
        "user_agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "viewport": {"width": 1280, "height": 800},
        "locale": "en-US",
    }
    # Tag browser traffic with the run id (User-Agent token) unless TEST_RUN_TAGGING=false.
    tagged = run_id.browser_user_agent(args.get("user_agent"))
    if tagged:
        args["user_agent"] = tagged
    return args


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

        evidence = item.stash.get(_EVIDENCE, None)
        from core import run_id
        from core.build_info import last_observed

        write_failure_bundle(
            test_name=item.name,
            error=call.excinfo.value if call.excinfo else Exception("unknown"),
            screenshot_bytes=screenshot_bytes,
            console_errors=list(evidence.console_errors) if evidence else [],
            failed_requests=list(evidence.failed_requests) if evidence else [],
            page_errors=list(evidence.page_errors) if evidence else [],
            dom_snapshot=dom_snapshot,
            context={"runId": run_id.run_id(), "deployedVersion": last_observed().describe()},
        )
