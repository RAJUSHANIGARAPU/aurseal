"""The user pool: parsing, preferred users, OS-lock leases, waiting, and login precedence."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from core.auth.settings import AuthMode, AuthSettings
from core.config_reader import ConfigReader
from gate import user_pool
from gate.user_pool import (
    PoolError,
    PoolExhausted,
    PoolUser,
    acquire,
    allowed_workers,
    lease_order,
    load_pool,
    parse_pool,
    pool_file_path,
    preferred_position,
    try_lease,
)

USERS = [
    PoolUser(1, "user1@example.test", "pw-one"),
    PoolUser(3, "user3@example.test", "pw-three"),
    PoolUser(7, "user7@example.test", "pw-seven"),
]


@pytest.fixture(autouse=True)
def no_process_lease(monkeypatch):
    monkeypatch.setattr(user_pool, "_process_lease", None)
    for name in ("AUTH_USERNAME", "AUTH_PASSWORD", "AUTH_MODE", "TEST_SHARD"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def leases():
    held: list = []
    yield held
    for lease in held:
        lease.release()


class TestParse:
    def test_ordered_by_n_with_gaps(self):
        users = parse_pool(
            {
                "POOL_7_USERNAME": "c@example.test",
                "POOL_7_PASSWORD": "p7",
                "POOL_2_PASSWORD": "p2",
                "POOL_2_USERNAME": "a@example.test",
            }
        )
        assert [(u.index, u.username) for u in users] == [
            (2, "a@example.test"),
            (7, "c@example.test"),
        ]

    @pytest.mark.parametrize(
        "values, message",
        [
            ({"POOL_1_USERNAME": "a@example.test"}, "POOL_1_USERNAME has no POOL_1_PASSWORD"),
            ({"POOL_4_PASSWORD": "pw"}, "POOL_4_PASSWORD has no POOL_4_USERNAME"),
            ({"POOL_1_USERNAME": "a@example.test", "POOL_1_PASSWORD": " "}, "has no"),
            ({"POOL_X_USERNAME": "a@example.test"}, "unexpected key POOL_X_USERNAME"),
            (
                {
                    "POOL_1_USERNAME": "a@example.test",
                    "POOL_1_PASSWORD": "pw",
                    "POOL_2_USERNAME": "A@example.test",
                    "POOL_2_PASSWORD": "pw",
                },
                "repeats POOL_1",
            ),
        ],
    )
    def test_broken_entries_are_errors_that_never_show_values(self, values, message):
        with pytest.raises(PoolError, match=message) as info:
            parse_pool(values)
        assert "pw" not in str(info.value).replace("PASSWORD", "")

    def test_file_next_to_the_env_file(self, tmp_path):
        assert pool_file_path("qa", tmp_path) == tmp_path / ".env.qa.pool"
        assert load_pool("qa", tmp_path) == []
        (tmp_path / ".env.qa.pool").write_text(
            "# pool\nPOOL_1_USERNAME=user1@example.test\nPOOL_1_PASSWORD='pw one'\n"
        )
        assert load_pool("qa", tmp_path) == [PoolUser(1, "user1@example.test", "pw one")]

    def test_repr_never_shows_the_password(self):
        assert "pw-one" not in repr(USERS[0])
        assert "pw-one" not in repr(user_pool.Lease(USERS[0], Path("user1.lock")))


class TestPreference:
    @pytest.mark.parametrize(
        "shard, position",
        [("2/3", 2), ("serial/3", 1), (None, None), ("", None), ("bogus", None)],
    )
    def test_preferred_position_from_the_shard_spec(self, shard, position):
        assert preferred_position(shard) == position

    def test_preferred_user_first_then_the_rest_in_order(self):
        assert [u.index for u in lease_order(USERS, 2)] == [3, 1, 7]
        assert [u.index for u in lease_order(USERS, None)] == [1, 3, 7]
        assert [u.index for u in lease_order(USERS, 5)] == [3, 1, 7]  # wraps around


class TestLeases:
    def test_two_leases_never_get_the_same_user(self, tmp_path, leases):
        first = try_lease(USERS, tmp_path)
        second = try_lease(USERS, tmp_path)
        leases += [first, second]
        assert first.user != second.user

    def test_a_held_user_is_skipped_even_when_preferred(self, tmp_path, leases):
        leases.append(try_lease(USERS, tmp_path, preferred=2))
        other = try_lease(USERS, tmp_path, preferred=2)
        leases.append(other)
        assert leases[0].user.index == 3 and other.user.index == 1

    def test_a_released_user_can_be_leased_again(self, tmp_path, leases):
        lease = try_lease(USERS[:1], tmp_path)
        assert try_lease(USERS[:1], tmp_path) is None
        lease.release()
        again = try_lease(USERS[:1], tmp_path)
        leases.append(again)
        assert again.user == USERS[0]

    def test_the_lock_file_names_its_holder(self, tmp_path, leases):
        lease = try_lease(USERS, tmp_path, holder={"pid": 42, "shard": "1/2"})
        leases.append(lease)
        held = json.loads(lease.path.read_text())
        assert held == {"pid": 42, "shard": "1/2", "username": "user1@example.test"}
        assert "pw-one" not in lease.path.read_text()

    def test_all_busy_waits_then_fails_naming_users_never_passwords(self, tmp_path, leases):
        leases += [try_lease(USERS, tmp_path) for _ in USERS]
        now, slept = [0.0], []

        def sleep(seconds):
            slept.append(seconds)
            now[0] += seconds

        with pytest.raises(PoolExhausted) as info:
            acquire(USERS, tmp_path, wait_s=12, poll_s=5, sleep=sleep, clock=lambda: now[0])
        message = str(info.value)
        assert all(u.username in message for u in USERS)
        assert not any(u.password in message for u in USERS)
        assert str(tmp_path) in message
        assert slept == [5, 5, 5]

    def test_a_user_freed_while_waiting_is_taken(self, tmp_path, leases):
        held = try_lease(USERS[:1], tmp_path)
        lease = acquire(USERS[:1], tmp_path, wait_s=60, poll_s=1, sleep=lambda _: held.release())
        leases.append(lease)
        assert lease.user == USERS[0]


class TestWorkers:
    def test_defaults_to_the_pool_size_capped_at_four(self):
        assert allowed_workers(2, None, needs_login=True) == 2
        assert allowed_workers(9, None, needs_login=True) == 4
        assert allowed_workers(0, None, needs_login=True) == 1

    def test_never_more_workers_than_users(self):
        with pytest.raises(ValueError, match="3 workers need 3 login users but the pool has 2"):
            allowed_workers(2, 3, needs_login=True)
        with pytest.raises(ValueError, match="no user pool"):
            allowed_workers(0, 2, needs_login=True)

    def test_without_a_login_users_do_not_limit_workers(self):
        assert allowed_workers(0, 6, needs_login=False) == 6

    def test_cli(self, tmp_path):
        (tmp_path / ".env.qa.pool").write_text(
            "POOL_1_USERNAME=user1@example.test\nPOOL_1_PASSWORD=pw\n"
        )
        out = io.StringIO()
        assert user_pool.main(["size", "--env", "qa"], out, root=tmp_path) == 0
        assert out.getvalue().strip() == "1"


def _reader(tmp_path, environments):
    envs = tmp_path / "environments.json"
    envs.write_text(
        json.dumps({"qa": {"base_url": "https://app.example.test/", "auth_mode": "ui"}})
    )
    return ConfigReader("qa", environments_file=envs, env_file_dir=environments)


class TestPrecedence:
    @pytest.fixture
    def pool_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USER_POOL_LEASE_DIR", str(tmp_path / "leases"))
        directory = tmp_path / "root"
        directory.mkdir()
        (directory / ".env.qa").write_text(
            "AUTH_USERNAME=fallback@example.test\nAUTH_PASSWORD=fallback-pw\n"
        )
        return directory

    def _release(self):
        if user_pool._process_lease is not None:
            user_pool._process_lease.release()

    def test_no_pool_file_keeps_the_old_behaviour(self, tmp_path, pool_dir):
        settings = AuthSettings.from_config(_reader(tmp_path, pool_dir))
        assert settings.username == "fallback@example.test"
        assert user_pool._process_lease is None

    def test_the_pool_beats_the_env_file(self, tmp_path, pool_dir, monkeypatch):
        (pool_dir / ".env.qa.pool").write_text(
            "POOL_1_USERNAME=user1@example.test\nPOOL_1_PASSWORD=pw-one\n"
        )
        monkeypatch.setenv("USER_POOL_LEASE_RECORD", str(tmp_path / "lease.json"))
        try:
            settings = AuthSettings.from_config(_reader(tmp_path, pool_dir))
            assert (settings.username, settings.password) == ("user1@example.test", "pw-one")
            # one lease per process: asking again returns the same user
            assert AuthSettings.from_config(_reader(tmp_path, pool_dir)).username == (
                "user1@example.test"
            )
            assert json.loads((tmp_path / "lease.json").read_text())["index"] == 1
        finally:
            self._release()

    def test_an_exported_username_wins_and_the_pool_is_not_touched(
        self, tmp_path, pool_dir, monkeypatch
    ):
        (pool_dir / ".env.qa.pool").write_text("POOL_1_USERNAME=user1@example.test\n")  # broken
        monkeypatch.setenv("AUTH_USERNAME", "ci@example.test")
        monkeypatch.setenv("AUTH_PASSWORD", "ci-pw")
        settings = AuthSettings.from_config(_reader(tmp_path, pool_dir))
        assert settings.username == "ci@example.test"
        assert user_pool._process_lease is None

    def test_a_mode_without_a_login_never_leases(self, tmp_path, pool_dir, monkeypatch):
        (pool_dir / ".env.qa.pool").write_text(
            "POOL_1_USERNAME=user1@example.test\nPOOL_1_PASSWORD=pw-one\n"
        )
        monkeypatch.setenv("AUTH_MODE", "none")
        settings = AuthSettings.from_config(_reader(tmp_path, pool_dir))
        assert settings.mode is AuthMode.NONE
        assert user_pool._process_lease is None
