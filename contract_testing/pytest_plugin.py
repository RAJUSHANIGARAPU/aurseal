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

  hybrid:
    - Does both: generate + validate in the same session

The gate (``fail_on_breaking``, default true, ``CONTRACT_FAIL_ON_BREAKING``):

  - A breaking diff against the stored contract, or a provider response that does
    not match it, sets the session exit status to 1, prints what broke, and is
    recorded as a ``contract`` verdict. The breaking contract is NOT saved, so the
    next run is red again until the change is accepted deliberately (rerun with
    ``CONTRACT_FAIL_ON_BREAKING=false``, which saves it as a new major version).
  - With the gate off, the same changes are reported and recorded; exit status is
    left alone.
  - A run whose contract equals the stored one saves nothing (no version bump).
  - Provider/hybrid mode with no captured interaction is "no contract evidence":
    reported and recorded as such, never as compatible.

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
        self._capture_keys: set[str] = set()
        self._validation_results: list = []
        self._contracts_saved: int = 0
        self._environment = "qa"
        self._store = None
        self._generator = None
        self._validator_contract = None
        self._breaking: list[str] = []
        self._provider_failures: list[str] = []
        self._interactions_seen = 0

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
            self._provider_failures = [
                f"{r.interaction_key}: {'; '.join(r.errors)}"
                for r in self._validation_results
                if not r.passed
            ]

        summary = reporter.generate_session_summary(
            self._config.consumer_name,
            self._config.provider_name,
            self._contracts_saved,
            self._validation_results,
        )
        print(f"\n{summary}")
        self._gate(session)

    # ------------------------------------------------------------------
    # The gate
    # ------------------------------------------------------------------

    def _gate(self, session) -> None:
        from verification.verdicts import ContractVerdict, Kind, record_verdict

        problems = [*self._breaking, *self._provider_failures]
        no_evidence = self._config.mode in ("provider", "hybrid") and not self._interactions_seen
        if problems:
            verdict = (
                ContractVerdict.BREAKING if self._breaking else ContractVerdict.PROVIDER_MISMATCH
            )
        elif no_evidence:
            verdict = ContractVerdict.NO_EVIDENCE
        else:
            verdict = ContractVerdict.COMPATIBLE
        subject = f"{self._config.consumer_name}->{self._config.provider_name}"
        record_verdict(
            Kind.CONTRACT,
            verdict,
            subject=subject,
            detail="; ".join(problems[:5]) or ("no interactions captured" if no_evidence else ""),
            nodeid=f"contract:{subject}",
        )
        if no_evidence and not problems:
            print("[contract] no contract evidence: no interaction was captured")
        if not problems:
            return
        print(f"[contract] {len(problems)} contract problem(s):")
        for problem in problems:
            print(f"  - {problem}")
        if self._config.fail_on_breaking:
            import pytest

            session.exitstatus = pytest.ExitCode.TESTS_FAILED
            print("[contract] failing the run (CONTRACT_FAIL_ON_BREAKING=true)")

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
        self._interactions_seen += len(interactions)
        self._retain(interactions)

        if self._config.mode in ("provider", "hybrid") and self._validator_contract:
            self._run_provider_validation(interactions)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _retain(self, interactions: list[dict]) -> None:
        """Keep one capture per interaction key, on the way in.

        ``from_captures`` throws duplicates away, but only at sessionfinish, so
        a five-hundred-test run over twenty endpoints held ten thousand captures
        — bodies included — to build twenty interactions. Deduplicating here
        keeps the same first-occurrence-wins order and the same key, so the
        contract that comes out is unchanged.
        """
        from contract_testing.consumer import normalize_path

        for raw in interactions:
            key = f"{raw.get('method', 'GET').upper()} {normalize_path(raw.get('path', '/'))}"
            if key in self._capture_keys:
                continue
            self._capture_keys.add(key)
            self._raw_captures.append(raw)

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
            if diff.change_type.value == "none":
                # Same contract: saving would only bump the patch version.
                logger.info("[contract] contract unchanged (v%s) — nothing saved", existing.version)
                return
            new_contract.version = diff.next_version(existing.version)
            reporter.write_diff_report(
                self._config.consumer_name,
                self._config.provider_name,
                existing.version,
                new_contract.version,
                diff,
            )
            if diff.has_breaking:
                self._breaking.extend(diff.breaking)
                if self._config.fail_on_breaking:
                    logger.warning(
                        "[contract] breaking change — v%s not saved; the stored v%s stays the "
                        "baseline",
                        new_contract.version,
                        existing.version,
                    )
                    return
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
