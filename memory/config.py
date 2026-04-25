"""Configuration for the Memory Intelligence Layer.

Controlled entirely via environment variables so the layer stays
optional and does not require changes to any existing config files.

Usage:
    cfg = MemoryConfig.from_env()   # reads ENABLE_MEMORY, MEMORY_MODE, etc.
    cfg = MemoryConfig(enabled=True, mode="active")  # programmatic
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class MemoryConfig:
    """All tunables for the memory layer.

    Defaults produce a completely disabled layer — the system behaves
    exactly as before when none of the env vars are set.
    """

    enabled: bool = False
    mode: str = "passive"  # "passive" (store-only) | "active" (store + retrieve + inject)
    db_path: Path = field(default_factory=lambda: Path("reports/memory/memory.db"))
    ttl_days: int = 90
    max_records_per_test: int = 20
    similarity_top_k: int = 3
    llm_enabled: bool = True

    @classmethod
    def from_env(cls) -> MemoryConfig:
        return cls(
            enabled=os.getenv("ENABLE_MEMORY", "false").lower() == "true",
            mode=os.getenv("MEMORY_MODE", "passive"),
            db_path=Path(os.getenv("MEMORY_DB_PATH", "reports/memory/memory.db")),
            ttl_days=int(os.getenv("MEMORY_TTL_DAYS", "90")),
            max_records_per_test=int(os.getenv("MEMORY_MAX_RECORDS_PER_TEST", "20")),
            similarity_top_k=int(os.getenv("MEMORY_TOP_K", "3")),
            llm_enabled=os.getenv("MEMORY_LLM_ENABLED", "true").lower() == "true",
        )
