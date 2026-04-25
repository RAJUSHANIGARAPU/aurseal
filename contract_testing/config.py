"""Configuration for the Contract Testing Layer.

All settings are read from environment variables so the layer stays
fully optional — no existing config files are touched.

Modes:
  consumer  — capture HTTP interactions, generate contracts from them
  provider  — validate live responses against stored contracts
  hybrid    — do both in the same session

Validation modes:
  strict    — response must match contract schema exactly (no extra fields)
  lenient   — only required fields and types are checked (extras allowed)

Storage backends:
  local     — filesystem under contracts/ directory (default)
  (remote/broker planned for future)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ContractConfig:
    enabled: bool = False
    mode: str = "consumer"  # "consumer" | "provider" | "hybrid"
    validation_mode: str = "lenient"  # "strict" | "lenient"
    storage: str = "local"  # "local" (only mode supported now)
    contracts_dir: Path = field(default_factory=lambda: Path("contracts"))
    consumer_name: str = "wiki_project"
    provider_name: str = "api"
    fail_on_breaking: bool = True  # CI: exit 1 when breaking changes detected
    openapi_spec: Path | None = None  # optional path to OpenAPI spec for schema validation

    @classmethod
    def from_env(cls) -> ContractConfig:
        spec_path = os.getenv("CONTRACT_OPENAPI_SPEC")
        return cls(
            enabled=os.getenv("ENABLE_CONTRACT_TESTING", "false").lower() == "true",
            mode=os.getenv("CONTRACT_MODE", "consumer"),
            validation_mode=os.getenv("CONTRACT_VALIDATION_MODE", "lenient"),
            storage=os.getenv("CONTRACT_STORAGE", "local"),
            contracts_dir=Path(os.getenv("CONTRACT_STORAGE_DIR", "contracts")),
            consumer_name=os.getenv("CONTRACT_CONSUMER", "wiki_project"),
            provider_name=os.getenv("CONTRACT_PROVIDER", "api"),
            fail_on_breaking=os.getenv("CONTRACT_FAIL_ON_BREAKING", "true").lower() == "true",
            openapi_spec=Path(spec_path) if spec_path else None,
        )
