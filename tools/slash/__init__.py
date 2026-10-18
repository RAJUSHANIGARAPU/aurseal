"""
Deterministic helpers behind the slash commands in ``.claude/commands/``.

A command file describes a procedure an assistant follows; the parts of that
procedure that have one right answer (counting JUnit results, choosing the
healing entries in a time window, deciding when a run loop stops, planning which
test modules a collection becomes) live here, as small modules with their own
command line and their own tests. Each module runs as ``python -m tools.slash.<name>``.
"""
