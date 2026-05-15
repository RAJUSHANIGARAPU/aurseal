# create-pr

After tests pass locally, open a GitHub pull request for the current branch using the `gh` CLI.
Attaches a test health summary and lists changed files in the PR description.

## Usage

```
/create-pr [title] [targetBranch]
```

Examples:
- `/create-pr` — auto-title from branch name, target = main
- `/create-pr "feat: article search tests" main`
- `/create-pr "fix: auth locator heal" develop`

---

## Phase 1 — Pre-flight checks

```bash
# Confirm we're on a feature branch (not main/develop)
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
echo "Branch: $CURRENT_BRANCH"
[[ "$CURRENT_BRANCH" == "main" || "$CURRENT_BRANCH" == "develop" ]] && \
  echo "ERROR: won't open PR from $CURRENT_BRANCH" && exit 1

# Confirm gh CLI is available
gh auth status 2>&1 | head -3

# Confirm there are commits ahead of target
TARGET=${2:-main}
AHEAD=$(git rev-list --count origin/$TARGET..$CURRENT_BRANCH 2>/dev/null || echo 0)
echo "Commits ahead of $TARGET: $AHEAD"
[[ "$AHEAD" -eq 0 ]] && echo "ERROR: nothing to merge" && exit 1
```

---

## Phase 2 — Run test health check

```bash
# Quick syntax check
python3 -m py_compile ui/tests/test_*.py api/tests/test_*.py 2>&1 | head -10

# Parse last junit XML results if available
python3 -c "
import glob, xml.etree.ElementTree as ET
files = glob.glob('target/junit/*.xml') + glob.glob('test-results/*.xml')
if not files:
    print('No test results found — run tests first or use /test-health-report.')
else:
    total = pass_ = fail = skip = 0
    for f in files:
        r = ET.parse(f).getroot()
        total += int(r.get('tests', 0))
        fail  += int(r.get('failures', 0)) + int(r.get('errors', 0))
        skip  += int(r.get('skipped', 0))
    pass_ = total - fail - skip
    print(f'Tests: {total}  Pass: {pass_}  Fail: {fail}  Skip: {skip}')
    print('GATE: PASS' if fail == 0 else f'GATE: BLOCK ({fail} failures)')
" 2>/dev/null || true
```

---

## Phase 3 — Build PR description

```bash
TARGET=${2:-main}
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)

# Changed files
git diff --name-only origin/$TARGET...$CURRENT_BRANCH | head -30

# Recent commits
git log --oneline origin/$TARGET...$CURRENT_BRANCH | head -10

# Healing events summary
python3 -c "
import json, glob
logs = glob.glob('target/logs/healing-events.log') + glob.glob('logs/healing-events.log')
if not logs:
    print('No healing events.')
else:
    events = [json.loads(l) for l in open(logs[0]) if l.strip()]
    print(f'AI self-heals: {len(events)}')
    for e in events[:3]:
        print(f'  - {e.get(\"locatorKey\",\"?\")} healed')
" 2>/dev/null || true
```

---

## Phase 4 — Push branch

```bash
git push -u origin "$(git rev-parse --abbrev-ref HEAD)"
```

---

## Phase 5 — Create GitHub PR via `gh` CLI

```bash
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
TARGET=${2:-main}
TITLE=${1:-"$(echo $CURRENT_BRANCH | sed 's/[-_\/]/ /g' | sed 's/feat /feat: /' | sed 's/fix /fix: /')"}

# Gather changed files for description
CHANGED=$(git diff --name-only origin/$TARGET...$CURRENT_BRANCH | sed 's/^/- /' | head -20)

# Gather test summary
TEST_SUMMARY=$(python3 -c "
import glob, xml.etree.ElementTree as ET
files = glob.glob('target/junit/*.xml') + glob.glob('test-results/*.xml')
if not files:
    print('No test results — run /test-health-report first.')
else:
    total = fail = skip = 0
    for f in files:
        r = ET.parse(f).getroot()
        total += int(r.get('tests',0))
        fail  += int(r.get('failures',0)) + int(r.get('errors',0))
        skip  += int(r.get('skipped',0))
    gate = 'PASS' if fail == 0 else f'BLOCK ({fail} failures)'
    print(f'Tests: {total}  Pass: {total-fail-skip}  Fail: {fail}  Skip: {skip}  Gate: {gate}')
" 2>/dev/null || echo "Run /test-health-report for details.")

gh pr create \
  --title "$TITLE" \
  --base "$TARGET" \
  --body "$(cat << BODY
## Summary
Auto-generated pull request from Claude Code skill \`/create-pr\`.

## Changed files
$CHANGED

## Test health
$TEST_SUMMARY

## Checklist
- [ ] Tests pass locally (run \`/test-health-report\`)
- [ ] No regressions in existing test suite
- [ ] Locator JSON committed if locators were healed
- [ ] Spec files committed if new scenarios were added

---
🤖 Generated with [Claude Code](https://claude.ai/claude-code)
BODY
)"
```

---

## Phase 6 — Report

```
✓ Branch pushed: <branch>
✓ PR opened: https://github.com/<org>/<repo>/pull/<number>
  Title:  <title>
  Target: <targetBranch>
  Tests:  <pass/fail summary>
```

---

## Notes

- Requires `gh` CLI authenticated: run `gh auth login` if not set up
- Run `/test-health-report` first — GATE=BLOCK means failing tests; fix before opening PR
- The `gh pr create` command uses the current git remote to auto-detect the repository
- Add `--reviewer <handle>` to the `gh pr create` command to request specific reviewers
- If the branch has no upstream, `gh pr create` will offer to push it automatically
