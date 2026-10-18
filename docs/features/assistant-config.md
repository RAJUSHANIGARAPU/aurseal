# Assistant project configuration

Two pieces of shared configuration for coding-assistant sessions in this repository: a
guard that keeps pushes off the default branch, and a lint for path-scoped rules files.
Server-side branch protection on the hosting service is the real control; these stop a
local session before it gets there.

---

## Push guard

### What it does

`tools.push_guard` blocks any `git push` whose destination resolves to the default branch,
by parsing the command rather than matching its spelling: `main`, `+main`, `:main`,
`HEAD:main`, `refs/heads/main`, any remote, force flags in any position, inside `&&`, `;`
or `|` chains, and a bare `git push` (or `git push origin`, or `HEAD`) while the current
branch is the default branch. `--all` and `--mirror` are blocked too. A quoted string
(`echo "git push origin main"`) is not a push. As a `PreToolUse` hook it reads the hook JSON
from stdin and exits 2 with a one-line reason to block, 0 to allow.

It also prints the matching `permissions.deny` list (remote `origin` and any remote, the
`HEAD:` forms, `-f`/`--force`/`--force-with-lease`), the string-matching first line of
defence the hook backs up.

### How to use

```bash
python -m tools.push_guard deny-list     # the permissions.deny block for .claude/settings.json
echo '{"tool_name": "Bash", "tool_input": {"command": "git push -f origin main"}}' | python -m tools.push_guard
```

Wiring, in `.claude/settings.json` (keep the existing `attribution` block):

```json
{
  "permissions": {"deny": ["...the deny-list output..."]},
  "hooks": {
    "PreToolUse": [
      {"matcher": "Bash",
       "hooks": [{"type": "command", "command": "python3 -m tools.push_guard"}]}
    ]
  }
}
```

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `PUSH_GUARD_BRANCH` | `main` | the protected default branch (read in one place) |

### Example

```text
push guard: git push +main targets main; open a pull request instead
```

---

## Path-scoped rules lint

### What it does

Topic notes that matter only when certain files are touched live in `.claude/rules/*.md`,
each starting with YAML front matter whose `paths:` lists the globs it applies to.
`tools.rules_lint` reports: no front matter; `paths` missing, empty or not strings; a glob
that matches no tracked file (a rule that applies to nothing); a rules file the project
guide (`CLAUDE.md`) does not name.

### How to use

```bash
python -m tools.rules_lint
python -m tools.rules_lint --dir .claude/rules --guide CLAUDE.md
```

### Configuration

`--dir` (default `.claude/rules`), `--guide` (default `CLAUDE.md`). Globs are relative to
the repository root; `**` spans directories, `*` stays in one segment.

### Example

```markdown
---
paths:
  - "ui/pages/**"
  - "ui/tests/**"
---
# Page objects and selectors
...
```
