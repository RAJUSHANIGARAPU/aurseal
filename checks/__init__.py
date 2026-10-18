"""Real test-type engines and their shared seams.

Data, contracts, databases, load, accessibility, visual, security, mutation and faults.
Each subpackage is a library that a pytest test, a CLI (``python -m checks.<kind>``)
and a plugin adapter under ``plugins/`` all call. Every engine reports a
:class:`checks._outcome.CheckOutcome` where it has a pass/fail verdict;
``checks._outcome.to_plugin_status`` maps it onto the plugin vocabulary the same way for
every kind, so a missing tool is always ``unknown`` and never ``pass``. Metric rows go
through :mod:`checks._sink`.

Third-party tools are optional (``requirements-checks.txt``) and imported lazily inside
functions, so importing ``checks`` never needs them.
"""
