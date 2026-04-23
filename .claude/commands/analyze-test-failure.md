# analyze-test-failure

Read pytest output / JUnit XML and diagnose test failures.

## Usage

```
/analyze-test-failure
```

## Steps

1. Find JUnit XML reports:
   ```bash
   ls reports/*.xml 2>/dev/null
   ```

2. Run AI diagnosis:
   ```bash
   python -c "
   from core.ai.log_analyzer import LogAnalyzer
   a = LogAnalyzer()
   print(a.analyze_failures())
   "
   ```

3. Read the recent log:
   ```bash
   tail -100 reports/logs/test.log
   ```

4. Cross-reference with `docs/ai_learnings.md`.

5. Provide root cause + exact code fix with file path and line number.
