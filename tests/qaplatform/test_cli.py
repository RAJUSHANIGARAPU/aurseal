"""The CLI's server-side commands (client commands are covered through PlatformClient)."""

from __future__ import annotations

import json

from qaplatform.cli import main
from qaplatform.store import PlatformStore


def test_init_and_token_lifecycle(db_path, capsys):
    assert main(["init", "--db", db_path]) == 0
    out = capsys.readouterr().out
    assert "qap_" in out and "shown once" in out
    assert main(["token", "create", "--db", db_path, "--role", "tester", "--label", "ci"]) == 0
    created = capsys.readouterr().out
    token_id = created.split("token id: ")[1].split()[0]
    assert main(["token", "list", "--db", db_path]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [t["id"] for t in listed] == [token_id]
    assert "token_hash" not in listed[0]
    assert main(["token", "revoke", "--db", db_path, token_id]) == 0
    assert main(["token", "revoke", "--db", db_path, token_id]) == 1
    store = PlatformStore.open(db_path)
    assert store.token(token_id)["revoked_at"]
    store.close()


def test_token_for_unknown_project(db_path, capsys):
    main(["init", "--db", db_path])
    assert main(["token", "create", "--db", db_path, "--project", "nope"]) == 1


def test_validate_command(tmp_path, capsys):
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"schema_version": "1.0", "name": "n"}))
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": "2.0"}))
    assert main(["validate", "run-create", str(good)]) == 0
    assert main(["validate", "run-create", str(bad)]) == 1
    assert "violation" in capsys.readouterr().out
