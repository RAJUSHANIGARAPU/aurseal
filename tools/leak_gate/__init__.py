"""Leak gate: keep a private source's names and code out of this repository.

Two checks, both fed by data that lives outside the repository:

* ``blocklist``: names and terms that must never appear (regexes, one per line);
* ``similarity``: winnowed fingerprints of a private source tree; any run of
  ten or more near-identical lines fails.

See ``tools/leak_gate/README.md``.
"""
