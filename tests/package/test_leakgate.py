"""Leak gate: matching, list loading, personal-data rules, allowlist, sources, output.

Every term here is synthetic (quux corp, zorblax, frobnitz_server). Personal-data samples
are assembled at run time, so this file itself stays clean under the gate's own tree scan.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from aurseal.leakgate import CANNOT_RUN, CLEAN, FINDINGS, NOT_ASSESSED, run
from aurseal.leakgate import denylist as deny
from aurseal.leakgate.__main__ import main
from aurseal.leakgate.normalise import line_tokens
from aurseal.leakgate.pii import iban_valid, line_rules

AT = "@"
LIST = "term:quux corp\nterm:zorblax\nqual:frobnitz_server.billing\nre:wibble-\\d{3}\n"


@pytest.fixture
def home(tmp_path) -> Path:
    """An empty home: no real list on the machine running the tests is ever read."""
    path = tmp_path / "home"
    path.mkdir()
    return path


def _hits(text: str, listing: str = LIST) -> list[tuple[int, str]]:
    matcher = deny.Matcher(deny.parse(listing, wordlist=deny.COMMON_WORDS))
    found = matcher.tokens(line_tokens(text)) + matcher.lines(text)
    return sorted((line, entry.rule) for line, entry in found)


def _scan(tmp_path, home, files: dict[str, str], listing: str | None = LIST, **kwargs):
    tree = tmp_path / "tree"
    for name, content in files.items():
        (tree / name).parent.mkdir(parents=True, exist_ok=True)
        (tree / name).write_text(content, encoding="utf-8")
    tree.mkdir(exist_ok=True)
    environ = {"HOME": str(home)}
    if listing is not None:
        environ[deny.ENV_VAR] = listing
    return run(trees=[tree], environ=environ, home=home, **kwargs)


# ------------------------------------------------------------------ 1-3: term matching


@pytest.mark.parametrize(
    "text", ["QuuxCorp", "quux_corp", "quux-corp.example", "QUUX  Corp", "quuxcorp"]
)
def test_a_term_hits_every_spelling(text):
    assert _hits(f"x = {text}\n") == [(1, "deny.term")]


def test_a_term_does_not_hit_a_longer_word():
    assert _hits("the quuxcorpus is fine\n") == []


@pytest.mark.parametrize("text", ["Ｚｏｒｂｌａｘ", "zor\u200bblax", "zоrblax"])
def test_unicode_tricks_still_hit(text):
    assert _hits(text) == [(1, "deny.term")]


def test_a_three_token_term_wrapped_across_a_line_break_hits_at_its_first_line():
    listing = "term:quux corp holdings\n"
    assert _hits("intro\nsee Quux\nCorp Holdings for details\n", listing) == [(2, "deny.term")]
    assert _hits("Quux\n\nCorp Holdings\n", listing) == []  # two line breaks apart


# ------------------------------------------------------------------ 4-5: loading, qual


def test_a_one_token_common_word_term_is_a_load_error(tmp_path, home):
    with pytest.raises(deny.DenylistError, match="line 2 is a common word") as info:
        deny.parse("term:zorblax\nterm:billing\n", wordlist=deny.COMMON_WORDS)
    assert "billing" not in str(info.value)
    outcome = _scan(tmp_path, home, {"a.txt": "x"}, listing="term:billing\n")
    assert outcome.code == CANNOT_RUN
    deny.parse("qual:frobnitz_server.billing\n", wordlist=deny.COMMON_WORDS)


def test_qual_hits_the_qualified_name_but_not_the_bare_word():
    assert _hits("from frobnitz_server.billing import X\n") == [(1, "deny.qual")]
    assert _hits("open('frobnitz_server/billing/api.py')\n") == [(1, "deny.qual")]
    assert _hits("import billing\nbilling_server = 1\n") == []


def test_a_bad_regex_is_named_by_line_only():
    with pytest.raises(deny.DenylistError, match="line 1 is not a valid regex") as info:
        deny.parse("re:secret([\n")
    assert "secret" not in str(info.value)


def test_list_sources_and_precedence(tmp_path, home):
    (home / ".config" / "leak-gate").mkdir(parents=True)
    (home / ".config" / "leak-gate" / "blocklist.txt").write_text("zorb.ax\n", "utf-8")
    legacy = deny.load(environ={}, home=home)
    assert legacy is not None and legacy.entries[0].kind == "re"
    assert "deprecated" in legacy.source
    (home / ".config" / "testkeel").mkdir(parents=True)
    (home / ".config" / "testkeel" / "denylist.txt").write_text("qual:zorb.ax\n", "utf-8")
    with pytest.warns(DeprecationWarning, match="move it to ~/.config/aurseal/denylist.txt"):
        previous = deny.load(environ={}, home=home)
    assert previous is not None and previous.entries[0].kind == "qual"
    assert previous.source == "~/.config/testkeel/denylist.txt (deprecated location)"
    (home / ".config" / "aurseal").mkdir(parents=True)
    (home / ".config" / "aurseal" / "denylist.txt").write_text("zorblax\n", "utf-8")
    current = deny.load(environ={}, home=home)
    assert current is not None and current.entries[0].kind == "term"
    from_env = deny.load(environ={deny.ENV_VAR: "qual:frobnitz_server.billing"}, home=home)
    assert from_env is not None and from_env.entries[0].kind == "qual"
    assert deny.load(environ={}, home=tmp_path / "nobody") is None


# ------------------------------------------------------------------ 6: missing list, fork


def test_a_missing_or_empty_list_fails_closed(tmp_path, home):
    assert _scan(tmp_path, home, {"a.txt": "x"}, listing=None).code == CANNOT_RUN
    assert _scan(tmp_path, home, {"a.txt": "x"}, listing="# only a comment\n").code == CANNOT_RUN


def test_missing_list_warn_runs_the_built_in_rules(tmp_path, home):
    outcome = _scan(tmp_path, home, {"a.txt": "x"}, listing=None, missing_list="warn")
    assert outcome.code == CLEAN
    assert any("warning" in note for note in outcome.notes)


def test_fork_mode_is_not_assessed_but_personal_data_still_fails(tmp_path, home):
    assert _scan(tmp_path, home, {"a.txt": "zorblax"}, listing=None, fork=True).code == (
        NOT_ASSESSED
    )
    leaked = {"a.txt": f"mail jane{AT}quuxcorp.io"}
    assert _scan(tmp_path, home, leaked, listing=None, fork=True).code == FINDINGS


# ------------------------------------------------------------------ 7: output hygiene


def test_the_output_names_locations_and_rule_ids_only(tmp_path, home, monkeypatch, capsys):
    tree = tmp_path / "tree"
    (tree / "docs").mkdir(parents=True)
    (tree / "docs" / "QuuxCorp-notes.md").write_text("plain words\n", "utf-8")
    (tree / "docs" / "readme.md").write_text("intro\nwe use zorblax daily\n", "utf-8")
    listing = tmp_path / "list.txt"
    listing.write_text(LIST, "utf-8")
    monkeypatch.setenv("HOME", str(home))
    code = main(["scan", "--tree", str(tree), "--denylist", str(listing)])
    out, err = capsys.readouterr()
    assert code == FINDINGS
    assert "docs/ (name #1) deny.term [e1]" in out
    assert "docs/readme.md:2 deny.term [e2]" in out
    for secret in ("quux", "Quux", "corp", "zorblax", "we use", "frobnitz", "wibble"):
        assert secret not in out + err


def test_content_inside_a_hit_file_name_is_reported_under_the_masked_name(tmp_path, home):
    outcome = _scan(tmp_path, home, {"QuuxCorp/notes.md": "zorblax\n"})
    locations = {str(f) for f in outcome.findings}
    assert locations == {"./ (name #1) deny.term [e1]", "(name #1)/notes.md:1 deny.term [e2]"}


# ------------------------------------------------------------------ 9-12: personal data


def _pii(line: str, path: str = "src/a.py", home: str | None = None) -> list[str]:
    return list(line_rules(path, home)(line))


@pytest.mark.parametrize(
    "address",
    [
        f"a{AT}example.com",
        f"x{AT}users.noreply.github.com",
        f"jane{AT}quuxmail.test",
        f"noreply{AT}github.com",
        f"support{AT}github.com",
        f"ops{AT}shop.example",
    ],
)
def test_reserved_and_public_addresses_are_clean(address):
    assert _pii(f"contact: {address}") == []


def test_a_real_looking_address_hits():
    assert _pii(f"contact: jane{AT}quuxcorp.io") == ["pii.email"]


def _iban(country: str, bban: str) -> str:
    digits = "".join(str(int(ch, 36)) for ch in bban + country + "00")
    return f"{country}{98 - int(digits) % 97:02d}{bban}"


def test_iban_needs_a_valid_checksum_and_length():
    valid = _iban("NL", "QUUX0123456789")
    assert iban_valid(valid)
    assert _pii(f"pay to {valid}") == ["pii.iban"]
    spaced = " ".join(valid[i : i + 4] for i in range(0, len(valid), 4))
    assert _pii(f"pay to {spaced}") == ["pii.iban"]
    broken = valid[:-1] + str((int(valid[-1]) + 1) % 10)
    assert _pii(f"pay to {broken}") == []
    assert _pii(f"pay to {_iban('NL', 'QUUX01234567890')}") == []  # wrong length for NL


@pytest.mark.parametrize(
    "number", ["+31 6 " + "12345678", "06" + "12345678", "+44 7700 " + "900123"]
)
def test_phone_numbers_hit(number):
    assert _pii(f"call {number} now") == ["pii.phone"]


@pytest.mark.parametrize(
    "text",
    [
        "v1.2.3456789",
        "1.31.612345678",
        "epoch 1760000000123",
        "sha 06123" + "45678" + "ab" * 15,
        "id 3f2b0612345678a",
        "build 2026-10-18",
        "0201234",
    ],
)
def test_versions_hashes_and_timestamps_are_not_phone_numbers(text):
    assert _pii(text) == []


def test_lock_files_are_not_checked_for_phone_numbers():
    assert _pii("hash +31 6 " + "12345678", path="uv.lock") == []


@pytest.mark.parametrize(
    ("line", "hit"),
    [
        ("/Us" + "ers/janedoe/x", True),
        ("C:\\Us" + "ers\\janedoe\\x", True),
        ("/ho" + "me/janedoe/app", True),
        ("/home/runner/work", False),
        ("/Users/<name>/project", False),
        ("/Users/$USER/project", False),
        ("${HOME}/project", False),
        ("https://shop.example/home/about", False),
    ],
)
def test_home_paths(line, hit):
    assert (_pii(line) == ["pii.home"]) is hit


def test_the_scanning_machines_own_home_hits():
    assert _pii("cache at /srv/quuxhome/cache", home="/srv/quuxhome") == ["pii.home"]
    assert _pii("cache at /home/runner/cache", home="/home/runner") == []


# ------------------------------------------------------------------ 13: allowlist


ALLOW = '[[allow]]\nrule = "pii.email"\npath = "tests/fixtures/**"\nreason = "invented"\n'


def test_the_allowlist_suppresses_a_rule_for_a_path(tmp_path, home):
    files = {
        "leakgate-allow.toml": ALLOW,
        "tests/fixtures/deep/users.txt": f"jane{AT}mailbox.io\n",
        "src/app.txt": f"jane{AT}mailbox.io\n",
    }
    outcome = _scan(tmp_path, home, files)
    assert [str(f) for f in outcome.findings] == ["src/app.txt:1 pii.email"]


@pytest.mark.parametrize(
    "entry",
    [
        '[[allow]]\nrule = "pii.email"\npath = "x/**"\n',
        '[[allow]]\nrule = "deny.term"\npath = "x/**"\nreason = "no"\n',
        '[[allow]]\nrule = "pii.nothing"\npath = "x"\nreason = "no"\n',
    ],
)
def test_a_bad_allow_entry_cannot_run(tmp_path, home, entry):
    assert _scan(tmp_path, home, {"leakgate-allow.toml": entry}).code == CANNOT_RUN


# ------------------------------------------------------------------ 14-15: range, messages


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=t",
         "-c", f"user.email=t{AT}example.com", "-C", str(repo), *args],
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip


def test_a_commit_range_scans_added_lines_messages_and_new_names(tmp_path, home):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("keep\nzorblax was here\n", "utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "first")
    base = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "a.txt").write_text("keep\nnew line\n", "utf-8")  # the term is only deleted
    _git(repo, "commit", "-qam", "drop the old line")
    environ = {"HOME": str(home), deny.ENV_VAR: LIST}
    assert run(commit_range=f"{base}..HEAD", range_root=repo, environ=environ).code == CLEAN

    (repo / "quux_corp.md").write_text("one\ntwo\nfrobnitz_server.billing\n", "utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "add notes\n\nported from zorblax")
    outcome = run(commit_range=f"{base}..HEAD", range_root=repo, environ=environ)
    shown = {str(f) for f in outcome.findings}
    sha = _git(repo, "rev-parse", "--short=7", "HEAD").strip()
    # a.txt changed in the range too, so the new file is the second name at the root.
    assert shown == {
        "./ (name #2) deny.term [e1]",
        "(name #2):3 deny.qual [e3]",
        f"commit {sha} msg:3 deny.term [e2]",
    }


def test_a_pr_body_message_file_is_located_by_label(tmp_path, home, monkeypatch, capsys):
    body = tmp_path / "body.txt"
    body.write_text("Summary\n\nport of the QuuxCorp fix\n", "utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv(deny.ENV_VAR, LIST)
    assert main(["scan", "--message-file", f"pr-body={body}"]) == FINDINGS
    out, err = capsys.readouterr()
    assert out.strip() == "pr-body:3 deny.term [e1]"
    assert "Quux" not in out + err
