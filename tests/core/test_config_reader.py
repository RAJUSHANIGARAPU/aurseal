"""The configuration ladder: environment, then .env.<env>, then environments.json."""

from __future__ import annotations

import json

import pytest

from core.config_reader import ConfigReader, ConfigSources, env_var_name, resolve_env
from core.env_file import load_env_file, parse_env_text
from tests.core._log_capture import framework_log


def _reader(os_env=None, env_file=None, file_config=None, env="qa") -> ConfigReader:
    return ConfigReader(
        env,
        sources=ConfigSources(
            os_env=os_env or {}, env_file=env_file or {}, file_config=file_config or {}
        ),
    )


class TestPrecedence:
    def test_environment_beats_env_file_beats_committed_file(self):
        reader = _reader(
            {"BASE_URL": "https://env.example.test"}, {"BASE_URL": "x"}, {"base_url": "y"}
        )
        assert reader.get("base_url") == "https://env.example.test"
        assert reader.source_of("base_url") == "env"

    def test_env_file_beats_committed_file(self):
        reader = _reader({}, {"BASE_URL": "https://file.example.test"}, {"base_url": "y"})
        assert reader.get_base_url() == "https://file.example.test"
        assert reader.source_of("base_url") == "env_file"

    def test_committed_file_is_the_floor(self):
        reader = _reader({}, {}, {"base_url": "https://committed.example.test"})
        assert reader.get("base_url") == "https://committed.example.test"

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_blank_counts_as_absent_at_every_rung(self, blank):
        """A CI parameter nobody filled in arrives as '' and must not shadow a real value."""
        reader = _reader({"AUTH_MODE": blank}, {"AUTH_MODE": blank}, {"auth_mode": "ui"})
        assert reader.get("auth_mode") == "ui"

    def test_dotted_keys_use_the_underscore_spelling(self):
        assert env_var_name("playwright.timeout.ms") == "PLAYWRIGHT_TIMEOUT_MS"
        reader = _reader({"PLAYWRIGHT_TIMEOUT_MS": "5000"})
        assert reader.get_int("playwright.timeout.ms", 30000) == 5000

    def test_missing_key_names_the_env_and_the_variable(self):
        with pytest.raises(KeyError, match=r"env=qa.*BASE_URL"):
            _reader().get("base_url")

    def test_default_when_nothing_answers(self):
        assert _reader().get("anything", "fallback") == "fallback"


class TestTypedGetters:
    @pytest.mark.parametrize(
        "raw, expected", [("true", True), ("YES", True), ("0", False), ("off", False)]
    )
    def test_booleans(self, raw, expected):
        assert _reader({"FLAG": raw}).get_bool("flag") is expected

    def test_junk_boolean_falls_back_to_default(self):
        assert _reader({"FLAG": "maybe"}).get_bool("flag", True) is True

    def test_junk_number_falls_back_to_default(self):
        assert _reader({"N": "ten"}).get_int("n", 3) == 3
        assert _reader({"N": "1.5"}).get_float("n", 0.0) == 1.5


class TestEnvironmentSelection:
    def test_explicit_wins(self):
        assert resolve_env("staging", {"TEST_ENV": "qa"}) == "staging"

    def test_test_env_when_not_explicit(self):
        assert resolve_env(None, {"TEST_ENV": "staging"}) == "staging"

    def test_default_is_qa(self):
        assert resolve_env(None, {}) == "qa"

    def test_unknown_env_fails_fast_listing_the_real_ones(self, tmp_path):
        environments = tmp_path / "environments.json"
        environments.write_text(json.dumps({"qa": {}, "staging": {}}))
        with pytest.raises(ValueError, match=r"Unknown env 'prod'.*\['qa', 'staging'\]"):
            ConfigReader("prod", environments_file=environments, env_file_dir=tmp_path)

    def test_real_files_are_read(self, tmp_path, monkeypatch):
        monkeypatch.delenv("BASE_URL", raising=False)
        environments = tmp_path / "environments.json"
        environments.write_text(json.dumps({"qa": {"base_url": "https://qa.example.test"}}))
        (tmp_path / ".env.qa").write_text("AUTH_USERNAME=tester@example.test\n")
        reader = ConfigReader("qa", environments_file=environments, env_file_dir=tmp_path)
        assert reader.get_base_url() == "https://qa.example.test"
        assert reader.get("auth_username") == "tester@example.test"

    def test_the_committed_environments_still_load(self, monkeypatch):
        monkeypatch.delenv("BASE_URL", raising=False)
        assert ConfigReader("qa").get_base_url().startswith("https://")


class TestEnvFile:
    def test_formats_real_files_use(self):
        text = (
            "# comment\n"
            "export A=1\n"
            "B = two words  # trailing comment\n"
            "C='single # not a comment'\n"
            'D="line\\nbreak"\n'
            "E=pa#ss\n"
            "not a pair\n"
        )
        assert parse_env_text(text) == {
            "A": "1",
            "B": "two words",
            "C": "single # not a comment",
            "D": "line\nbreak",
            "E": "pa#ss",
        }

    def test_missing_file_is_empty(self, tmp_path):
        assert load_env_file("qa", tmp_path) == {}

    def test_malformed_line_is_reported_without_its_content(self):
        with framework_log() as log:
            parse_env_text("SECRET hunter2\n")
        assert "line 1" in log.text
        assert "hunter2" not in log.text


class TestCommittedEnvironments:
    """Every committed environment carries the same profile keys."""

    @staticmethod
    def _committed() -> dict:
        from core.config_reader import _ENVIRONMENTS_FILE

        return json.loads(_ENVIRONMENTS_FILE.read_text(encoding="utf-8"))

    def test_every_env_defines_the_same_key_set(self):
        environments = self._committed()
        key_sets = {env: frozenset(entry) for env, entry in environments.items()}
        assert len(set(key_sets.values())) == 1, key_sets

    def test_every_env_satisfies_the_profile_contract(self):
        from core.env_profile import missing_keys

        for env, entry in self._committed().items():
            assert missing_keys(entry) == [], env

    def test_a_local_env_exists_and_targets_loopback(self):
        assert self._committed()["local"]["base_url"].startswith("http://localhost:")

    @pytest.mark.parametrize("env", ["local", "qa", "staging"])
    def test_every_committed_env_builds_a_profile(self, env, monkeypatch):
        from core.env_profile import REQUIRED_KEYS, EnvProfile

        for key in REQUIRED_KEYS:
            monkeypatch.delenv(env_var_name(key), raising=False)
        profile = EnvProfile.from_config(ConfigReader(env))
        assert profile.env == env
        assert profile.timeout_ms > 0
        assert profile.playwright_launch_args() == {
            "headless": profile.headless,
            "slow_mo": profile.slow_mo_ms,
        }

    def test_unknown_env_lists_the_committed_ones(self):
        with pytest.raises(ValueError, match=r"Unknown env 'prod'.*'local'.*'qa'.*'staging'"):
            ConfigReader("prod")


class TestEnvProfile:
    _ENTRY = {
        "base_url": "https://app.example.test/",
        "api_base_url": "https://api.example.test/",
        "browser": "chromium",
        "headless": True,
        "timeout_ms": 30000,
        "slow_mo_ms": 0,
        "video": "retain-on-failure",
        "tracing": "on",
        "ai_model": "model-x",
        "ai_timeout_s": 30,
        "auth_mode": "none",
        "read_only": False,
        "api_assertions_enabled": True,
    }

    def test_the_ladder_overrides_profile_values(self):
        reader = _reader({"TIMEOUT_MS": "60000", "HEADLESS": "false"}, {}, self._ENTRY)
        from core.env_profile import EnvProfile

        profile = EnvProfile.from_config(reader)
        assert profile.timeout_ms == 60000
        assert profile.headless is False
        assert profile.tracing == "on"

    def test_a_missing_key_raises_instead_of_defaulting(self):
        from core.env_profile import EnvProfile

        entry = {k: v for k, v in self._ENTRY.items() if k != "ai_timeout_s"}
        with pytest.raises(KeyError, match="AI_TIMEOUT_S"):
            EnvProfile.from_config(_reader({}, {}, entry))

    @pytest.mark.parametrize(
        ("key", "value", "message"),
        [
            ("headless", "ture", "not a boolean"),
            ("timeout_ms", "30s", "not an integer"),
            ("browser", "netscape", "expected"),
            ("video", "sometimes", "expected"),
        ],
    )
    def test_an_unusable_value_is_named(self, key, value, message):
        from core.env_profile import EnvProfile

        with pytest.raises(ValueError, match=message):
            EnvProfile.from_config(_reader({}, {}, {**self._ENTRY, key: value}))
