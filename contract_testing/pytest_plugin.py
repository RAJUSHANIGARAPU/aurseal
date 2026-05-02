"""Pytest plugin for automated contract testing.

Registration (via conftest.py when ENABLE_CONTRACT_TESTING=true):
    plugin = ContractPlugin.from_config(contract_config)
    config.pluginmanager.register(plugin, "contract-testing")

Behaviour per mode:

  consumer:
    - Installs HTTP capture patch at session start
    - Activates capture before each test, collects after
    - At session end: generates contracts from all captures, saves + diffs

  provider:
    - Installs HTTP capture patch at session start
    - Validates each response against latest stored contract
    - Reports failures without stopping the test (unless fail_on_breaking=True)

  hybrid:
    - Does both: generate + validate in the same session

Zero friction — no test markers or fixtures required.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contract_testing.config import ContractConfig

logger = logging.getLogger(__name__)


class ContractPlugin:
    """Pytest plugin that drives contract generation and validation."""

    def __init__(self, config: ContractConfig) -> None:
        self._config = config
        self._raw_captures: list[dict] = []
        self._validation_results: list = []
        self._contracts_saved: int = 0
        self._environment = "qa"
        self._store = None
        self._generator = None
        self._validator_contract = None

    @classmethod
    def from_config(cls, config: ContractConfig) -> ContractPlugin:
        return cls(config)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def pytest_sessionstart(self, session) -> None:  # noqa: ARG002
        from contract_testing import capture
        from contract_testing.consumer import ConsumerContractGenerator
        from contract_testing.storage import ContractStore

        self._store = ContractStore(self._config.contracts_dir)
        self._generator = ConsumerContractGenerator(
            consumer=self._config.consumer_name,
            provider=self._config.provider_name,
        )

        is_consumer = self._config.mode in ("consumer", "hybrid")
        is_provider = self._config.mode in ("provider", "hybrid")

        if is_consumer or is_provider:
            capture.install()
            logger.debug("[contract] HTTP capture patch installed")

        if is_provider:
            existing = self._store.load_latest(
                self._config.consumer_name, self._config.provider_name
            )
            self._validator_contract = existing
            if existing:
                logger.debug(
                    "[contract] loaded contract v%s for provider validation", existing.version
                )
            else:
                logger.info("[contract] no existing contract found — provider validation skipped")

    def pytest_sessionfinish(self, session, exitstatus) -> None:  # noqa: ARG002
        from contract_testing import capture
        from contract_testing.reporter import ContractReporter

        capture.remove()

        reporter = ContractReporter()

        if self._config.mode in ("consumer", "hybrid") and self._raw_captures:
            self._save_consumer_contract(reporter)

        if self._config.mode in ("provider", "hybrid") and self._validation_results:
            reporter.write_validation_report(
                self._config.consumer_name,
                self._config.provider_name,
                self._validation_results,
            )

        summary = reporter.generate_session_summary(
            self._config.consumer_name,
            self._config.provider_name,
            self._contracts_saved,
            self._validation_results,
        )
        print(f"\n{summary}")

    # ------------------------------------------------------------------
    # Per-test hooks
    # ------------------------------------------------------------------

    def pytest_runtest_setup(self, item) -> None:
        if self._config.mode in ("consumer", "hybrid", "provider"):
            from contract_testing import capture

            capture.activate(test_name=item.nodeid)

    def pytest_runtest_teardown(self, item, nextitem) -> None:  # noqa: ARG002
        if self._config.mode not in ("consumer", "hybrid", "provider"):
            return

        from contract_testing import capture

        interactions = capture.deactivate()
        self._raw_captures.extend(interactions)

        if self._config.mode in ("provider", "hybrid") and self._validator_contract:
            self._run_provider_validation(interactions)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _save_consumer_contract(self, reporter) -> None:
        from contract_testing.differ import ContractDiffer

        assert self._generator is not None
        assert self._store is not None

        new_contract = self._generator.from_captures(self._raw_captures)
        if not new_contract.interactions:
            logger.info("[contract] no interactions captured — nothing to save")
            return

        existing = self._store.load_latest(self._config.consumer_name, self._config.provider_name)

        if existing:
            differ = ContractDiffer()
            diff = differ.diff(existing, new_contract)
            new_contract.version = diff.next_version(existing.version)
            if diff.change_type.value != "none" or not self._store.exists(
                self._config.consumer_name, self._config.provider_name
            ):
                reporter.write_diff_report(
                    self._config.consumer_name,
                    self._config.provider_name,
                    existing.version,
                    new_contract.version,
                    diff,
                )
        self._store.save(new_contract)
        self._contracts_saved += 1

    def _run_provider_validation(self, interactions: list[dict]) -> None:
        from contract_testing.provider import ProviderContractValidator

        if not self._validator_contract:
            return

        validator = ProviderContractValidator(
            self._validator_contract,
            validation_mode=self._config.validation_mode,
        )

        for raw in interactions:
            result = validator.validate_response(
                method=raw.get("method", "GET"),
                path=raw.get("path", "/"),
                status=raw.get("status", 200),
                response_body=raw.get("response_body"),
            )
            # Skip "no contract" pseudo-results
            if result.errors != ["(no contract for this endpoint)"]:
                self._validation_results.append(result)
