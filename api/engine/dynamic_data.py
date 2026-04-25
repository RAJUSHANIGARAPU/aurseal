"""Resolve {{variable}} placeholders via Faker, ContextMemory, or env vars."""

import logging
import os
import re
from typing import TYPE_CHECKING, Any

from faker import Faker

if TYPE_CHECKING:
    from api.engine.context_memory import ContextMemory

logger = logging.getLogger(__name__)

_PATTERN = re.compile(r"\{\{(\w+)\}\}")

FAKER_MAP: dict[str, str] = {
    "name": "name",
    "email": "email",
    "phone": "phone_number",
    "address": "address",
    "company": "company",
    "uuid": "uuid4",
    "first_name": "first_name",
    "last_name": "last_name",
    "username": "user_name",
    "password": "password",
    "url": "url",
    "date": "date",
    "timestamp": "iso8601",
    "integer": "random_int",
    "string": "word",
    "text": "sentence",
    "token": "sha256",
}


class DynamicDataEngine:
    """Resolves {{variable}} placeholders in strings.

    Resolution priority:
    1. extra dict passed at call time
    2. ContextMemory store
    3. Environment variables
    4. Faker-generated value based on FAKER_MAP
    5. Leaves placeholder unchanged and logs a warning
    """

    def __init__(self, memory: "ContextMemory | None" = None) -> None:
        self._memory = memory
        self._faker = Faker()

    def resolve(self, text: str, extra: dict | None = None) -> str:
        """Replace every {{variable}} in text with a resolved value."""

        def replace_match(match: re.Match) -> str:
            var = match.group(1)
            value = self._resolve_var(var, extra or {})
            if value is None:
                logger.warning("Could not resolve variable '%s' — leaving as-is", var)
                return match.group(0)
            return str(value)

        return _PATTERN.sub(replace_match, text)

    def _resolve_var(self, var: str, extra: dict[str, Any]) -> Any:
        # 1. extra dict
        if var in extra:
            return extra[var]

        # 2. ContextMemory
        if self._memory is not None:
            val = self._memory.get(var)
            if val is not None:
                return val

        # 3. Environment variable
        env_val = os.environ.get(var)
        if env_val is not None:
            return env_val

        # 4. Faker map
        faker_attr = FAKER_MAP.get(var)
        if faker_attr:
            try:
                return getattr(self._faker, faker_attr)()
            except AttributeError:
                logger.warning("Faker has no attribute '%s' for variable '%s'", faker_attr, var)

        return None
