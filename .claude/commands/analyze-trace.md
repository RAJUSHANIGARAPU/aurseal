# analyze-trace

Analyze the latest Playwright trace ZIP and diagnose what happened.

## Usage

```
/analyze-trace [path/to/trace.zip]
```

If no path given, uses the most recent file in `reports/traces/`.

## Steps

1. Find the trace:
   ```bash
   ls -t reports/traces/*.zip | head -3
   ```

2. Run the Python analyzer:
   ```bash
   python -c "
   from core.ai.trace_analyzer import TraceAnalyzer
   t = TraceAnalyzer()
   print(t.analyze())
   "
   ```

3. Or inspect manually:
   ```bash
   unzip -o reports/traces/<trace>.zip -d /tmp/trace-inspect/
   ls /tmp/trace-inspect/
   ```

4. Read `docs/ai_learnings.md` to match the failure pattern to known issues.

5. Report: what actions ran, what failed, root cause, exact fix.
