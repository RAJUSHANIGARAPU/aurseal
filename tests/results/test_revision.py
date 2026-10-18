"""Revision hashes: git's blob formula, normalised whitespace, decorators count."""

from __future__ import annotations

from results.revision import blob_sha, file_revision, function_revision, normalise_source

# Pinned once from `printf '<text>' | git hash-object --stdin`.
GIT_EMPTY = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
GIT_TEST_A = "d6a29c342010d56d187a89babdf2b94468c1d0eb"  # "def test_a():\n    assert 1\n"
GIT_DECORATED = "835c397397ebfb1ef08e1c073be4002a526d2a83"  # with "@pytest.mark.smoke\n"


def test_blob_sha_equals_git_hash_object():
    assert blob_sha("") == GIT_EMPTY
    assert blob_sha("def test_a():\n    assert 1\n") == GIT_TEST_A
    assert blob_sha(b"@pytest.mark.smoke\ndef test_a():\n    assert 1\n") == GIT_DECORATED


def test_whitespace_only_and_crlf_changes_give_the_same_hash():
    base = normalise_source("def test_a():\n    assert 1\n")
    assert blob_sha(base) == GIT_TEST_A
    for variant in (
        "def test_a():   \r\n    assert 1\r\n",
        "    def test_a():\n        assert 1\n",  # indented, as inside a class
        "\n\ndef test_a():\n    assert 1\n\n\n",
    ):
        assert blob_sha(normalise_source(variant)) == GIT_TEST_A


def test_a_changed_body_gives_a_new_hash():
    assert blob_sha(normalise_source("def test_a():\n    assert 2\n")) != GIT_TEST_A


def _plain():
    assert 1


def _decorated_source_owner():
    import functools

    @functools.cache
    def inner():
        return 1

    return inner


def test_function_revision_includes_decorators():
    plain = function_revision(_plain)
    decorated = function_revision(_decorated_source_owner())
    assert len(plain) == 40 and plain != decorated
    assert function_revision(_plain) == plain  # deterministic


def test_file_revision_is_git_hash_object_of_the_bytes(tmp_path):
    p = tmp_path / "f.py"
    p.write_bytes(b"def test_a():\n    assert 1\n")
    assert file_revision(p) == GIT_TEST_A
