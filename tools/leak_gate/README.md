# Leak gate

Keeps a private source's names and code out of this public repository. The
data each check needs lives **outside** the repository; the repository holds
only the checkers.

| Check | Data (default path) | Override |
|---|---|---|
| Name blocklist | `~/.config/leak-gate/blocklist.txt` | `LEAK_GATE_BLOCKLIST` |
| Code similarity | `~/.config/leak-gate/fingerprints.bin` | `LEAK_GATE_FINGERPRINTS` |

Both checks **fail closed**: missing, empty or unreadable data exits 2 with a
message saying where the file is expected. An unmeasured change is not a clean
one.

## Name blocklist

```bash
python -m tools.leak_gate.blocklist --staged              # what pre-commit runs
python -m tools.leak_gate.blocklist --range BASE..HEAD    # added lines + commit messages
python -m tools.leak_gate.blocklist --tree                # every tracked/untracked file
python -m tools.leak_gate.blocklist --message-file FILE   # what the commit-msg hook runs
```

File format: one case-insensitive Python regex per line, `#` starts a comment
line. File names are checked as well as content. Lockfiles and binary files are
skipped.

Findings print `path:line` and the blocklist **entry number**, never the
matched text or the pattern, so a CI log does not republish the names.

### Why a file outside the repo, not committed salted hashes

Hashing tokens and bigrams of the scanned text and comparing them with
committed salted hashes was considered and rejected:

* **Hashes of names are not secret.** The entries are short, guessable words
  (a company name, a product name, a city). The salt has to be committed next
  to the hashes for the check to work, so anyone can hash a candidate list and
  confirm which names the repo is guarding. The committed file would itself be
  a leak, in a slower format.
* **Hashes cannot hold patterns.** Several entries are regexes (word
  boundaries, `prefix-\d+` ticket shapes, optional separators); a token hash
  matches only exact tokens, so the gate would get weaker, not safer.

The plain file keeps full regex power and puts nothing derived from the names
in the repository. The cost is that every machine running the gate needs the
file; the hook fails closed so that cost is visible, not silent.

## Code similarity

```bash
# once, and again whenever the private tree moves (read-only on that tree):
git -C /path/to/private archive origin/main | tar -x -C "$TMP"
python -m tools.leak_gate.similarity build "$TMP" && rm -rf "$TMP"

python -m tools.leak_gate.similarity check --staged            # what pre-commit runs
python -m tools.leak_gate.similarity check --range BASE..HEAD
python -m tools.leak_gate.similarity check --tree [--paths a.py b.py]
```

`build` writes only 64-bit hashes (blake2b) grouped per file, with no path,
name or text; files are numbered. `check` prints `path:start-end` of this
repo and the private file number, never private text.

How it decides (details in `fingerprint.py`):

1. **Tokens are words.** Identifiers and numbers, case- and
   separator-folded (`contractNumber` = `contract_number`). Punctuation,
   quotes and comment markers are dropped; words inside strings and comments
   count. Declaration/type keywords a port rewrites (`def`/`function`,
   `self`/`this`, `int`/`str`, `None`/`null`, ...) are dropped.
2. **k-grams: K = 10 tokens, winnowing window W = 8.** The private side keeps
   only winnowed hashes (about 2/(W+1) of all k-grams); this side hashes
   every k-gram, so any kept private fingerprint is found. Any shared run of
   W + K - 1 = 17 tokens is guaranteed to keep a fingerprint.
3. **Lines.** Every token sits on one line; a k-gram covers the lines from
   its first to its last token. A line is *matched* when a k-gram whose hash
   is a private fingerprint covers it. *Code lines* are lines holding at least
   one token (blank and punctuation-only lines are ignored).
4. **Runs.** Matched code lines form one run while at most 2 unmatched code
   lines separate them (a small edit breaks a few k-grams, not the copy). A
   run with **>= 10 matched code lines fails**. Because kept fingerprints are
   at most W apart and K >= W, a copied region shows up contiguous except
   up to W - 1 = 7 tokens at each edge, so a verbatim copy needs roughly
   11-12 lines to be certain to trip the 10-line rule.
5. **Diff modes** (`--staged`, `--range`) fingerprint the whole changed file
   but fail only runs that touch an added line.

What it does not catch: a port that rewrites every line (types, names and
order), and copies shorter than the threshold. It is a tripwire for pasted
or lightly edited code and prose, not a proof of independent authorship.

## Pre-commit

`.pre-commit-config.yaml` runs the checks as local hooks
(`language: system`, so they use the active virtualenv). Both checks run on
staged content; the blocklist also checks the commit message. The commit-message
hook needs the commit-msg hook type installed:

```bash
pre-commit install --hook-type pre-commit --hook-type commit-msg
```

## CI

The job snippet lives outside this repository's workflows until the
decisions below are made. It runs `--range` over the pushed or PR commits and
**fails, never skips, when its data is missing**.

| Secret | Holds |
|---|---|
| `LEAK_GATE_BLOCKLIST` | the blocklist file's text (small, fits a secret) |
| `LEAK_GATE_FINGERPRINTS_PASSPHRASE` | passphrase for `fingerprints.bin.gpg` |

The fingerprint store is about 1 MB, far over the 48 KB limit of a GitHub
Actions secret, so it cannot be a secret itself: CI decrypts a
`gpg --symmetric` copy with the passphrase secret. Where that encrypted copy
lives is a decision below.

## Decision needed

1. **Should derived data leave the laptop at all?** CI needs the blocklist and
   the fingerprints. Storing them in CI secrets (and an encrypted store
   somewhere CI can fetch) moves data derived from the private source off
   this machine to the CI provider. The hashes are one-way, but a 10-token
   hash can still confirm a guessed snippet. If the answer is no, keep the
   gate local (pre-commit, fail closed) and do not add the CI job.
2. **Where the encrypted fingerprint store lives** if CI gets it: committed
   to this public repo as `fingerprints.bin.gpg` (GitHub's documented
   large-secret pattern, but encrypted derived data becomes public and
   permanent in history), a private repository or storage bucket fetched with
   a token secret, or nowhere (CI runs the blocklist only).
3. **Forked pull requests** get no secrets, so the job fails on them by
   design. Acceptable for a single-author repo; revisit if outside PRs start.
