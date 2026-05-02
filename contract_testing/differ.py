"""Breaking change detection between two contract versions.

Classification rules (provider-perspective):

BREAKING:
  - Interaction removed (endpoint the consumer expected is gone)
  - Response status code changed
  - Required field removed from response body schema
  - Field type changed in response body schema

NON-BREAKING:
  - New interaction added (provider exposes more)
  - New optional/required field added to response (additive)
  - Consumer-side request field removed (consumer relaxed requirement)

The consumer's required response fields are the contract. A provider that
removes or changes them breaks the consumer. A provider that adds them is
fine.
"""

from __future__ import annotations

from contract_testing.models import Contract, ContractDiff, Interaction


class ContractDiffer:
    """Computes a ContractDiff between an old and a new contract version."""

    def diff(self, old: Contract, new: Contract) -> ContractDiff:
        result = ContractDiff()

        old_by_key = {i.key: i for i in old.interactions}
        new_by_key = {i.key: i for i in new.interactions}

        # Removed interactions — BREAKING
        for key in old_by_key:
            if key not in new_by_key:
                result.breaking.append(f"Interaction removed: {key}")

        # Added interactions — non-breaking
        for key in new_by_key:
            if key not in old_by_key:
                result.non_breaking.append(f"New interaction: {key}")

        # Changed interactions
        for key in old_by_key.keys() & new_by_key.keys():
            breaking, non_breaking = self._diff_interaction(old_by_key[key], new_by_key[key])
            result.breaking.extend(breaking)
            result.non_breaking.extend(non_breaking)

        return result

    # ------------------------------------------------------------------
    # Interaction-level comparison
    # ------------------------------------------------------------------

    def _diff_interaction(self, old: Interaction, new: Interaction) -> tuple[list[str], list[str]]:
        breaking: list[str] = []
        non_breaking: list[str] = []

        key = old.key

        # Status code
        if old.response.status != new.response.status and old.response.status:
            breaking.append(
                f"[{key}] Status code changed: {old.response.status} → {new.response.status}"
            )

        # Response body schema
        b, nb = self._diff_schema(
            old.response.body_schema,
            new.response.body_schema,
            context=f"[{key}] response",
        )
        breaking.extend(b)
        non_breaking.extend(nb)

        return breaking, non_breaking

    def _diff_schema(
        self, old_schema: dict, new_schema: dict, context: str
    ) -> tuple[list[str], list[str]]:
        breaking: list[str] = []
        non_breaking: list[str] = []

        if not old_schema and not new_schema:
            return [], []

        # Schema type changed at top level
        old_type = old_schema.get("type")
        new_type = new_schema.get("type")
        if old_type and new_type and old_type != new_type:
            breaking.append(f"{context}: type changed {old_type!r} → {new_type!r}")
            return breaking, non_breaking

        if old_schema.get("type") != "object" and new_schema.get("type") != "object":
            return [], []

        old_required = set(old_schema.get("required", []))
        new_required = set(new_schema.get("required", []))
        old_props = old_schema.get("properties", {})
        new_props = new_schema.get("properties", {})

        # Fields the consumer required that the provider no longer exposes
        for field in old_required - new_required:
            breaking.append(f"{context}: required field '{field}' removed from response")

        # New required fields in response (additive — non-breaking)
        for field in new_required - old_required:
            non_breaking.append(f"{context}: new required field '{field}' in response")

        # Type changes in existing fields
        for field in old_required & new_required:
            old_f = old_props.get(field, {})
            new_f = new_props.get(field, {})
            ft_old = old_f.get("type")
            ft_new = new_f.get("type")
            if ft_old and ft_new and ft_old != ft_new:
                breaking.append(f"{context}: field '{field}' type changed {ft_old!r} → {ft_new!r}")
            # Recurse into nested objects
            if old_f.get("type") == "object" and new_f.get("type") == "object":
                b, nb = self._diff_schema(old_f, new_f, context=f"{context}.{field}")
                breaking.extend(b)
                non_breaking.extend(nb)

        return breaking, non_breaking
