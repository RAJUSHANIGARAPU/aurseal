"""MemoryMiddleware-style integration for the API Orchestrator.

Called at two points in the orchestrator loop:
  - after_ingestion(): generate contracts from Postman requests (static)
  - after_execution(): generate contracts from live captures (dynamic)

Both calls are no-ops when contract testing is disabled.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from api.agents.ingestion import PostmanRequest
    from contract_testing.config import ContractConfig

logger = logging.getLogger(__name__)


class ContractMiddleware:
    """Plugs contract generation/validation into the Orchestrator."""

    def __init__(self, config: ContractConfig) -> None:
        self._config = config
        from contract_testing.consumer import ConsumerContractGenerator
        from contract_testing.storage import ContractStore

        self._store = ContractStore(config.contracts_dir)
        self._generator = ConsumerContractGenerator(
            consumer=config.consumer_name,
            provider=config.provider_name,
        )

    def after_ingestion(self, requests: list[PostmanRequest]) -> None:
        """Generate a static contract from the Postman collection (no HTTP calls)."""
        if self._config.mode not in ("consumer", "hybrid"):
            return

        contract = self._generator.from_postman_requests(requests)
        if not contract.interactions:
            return

        existing = self._store.load_latest(self._config.consumer_name, self._config.provider_name)
        if existing:
            from contract_testing.differ import ContractDiffer

            diff = ContractDiffer().diff(existing, contract)
            contract.version = diff.next_version(existing.version)
        self._store.save(contract)
        logger.info(
            "[contract] saved static contract v%s (%d interactions)",
            contract.version,
            len(contract.interactions),
        )
