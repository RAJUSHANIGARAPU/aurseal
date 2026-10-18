"""The committed .env template covers every key the auth ladder reads, with placeholders only."""

from __future__ import annotations

from pathlib import Path

from core.auth.settings import AuthSettings
from core.config_reader import ConfigReader, ConfigSources, env_var_name
from core.env_file import parse_env_text

_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE = _ROOT / "config" / "env" / ".env.example"
_PLACEHOLDER = "REPLACE_ME"
# Keys the committed environments.json owns; a local file need not repeat them.
_COMMITTED_ONLY = {"base_url"}


class _RecordingReader(ConfigReader):
    def __init__(self) -> None:
        super().__init__("qa", sources=ConfigSources(os_env={}))
        self.keys: list[str] = []

    def get(self, key, default=None):  # type: ignore[override]
        self.keys.append(key)
        return default


def _ladder_keys() -> set[str]:
    reader = _RecordingReader()
    AuthSettings.from_config(reader)
    return {env_var_name(k) for k in reader.keys if k not in _COMMITTED_ONLY}


def _template() -> dict[str, str]:
    return parse_env_text(_TEMPLATE.read_text(encoding="utf-8"))


def test_every_key_the_ladder_reads_is_in_the_template():
    missing = _ladder_keys() - set(_template())
    assert not missing, f"add to {_TEMPLATE.name}: {sorted(missing)}"


def test_the_template_has_no_keys_nothing_reads():
    assert set(_template()) - _ladder_keys() == set()


def test_every_value_is_a_placeholder():
    real = {k: v for k, v in _template().items() if v != _PLACEHOLDER}
    assert not real, f"non-placeholder values in the template: {sorted(real)}"


def test_gitignore_ignores_real_env_files_but_not_the_template():
    rules = [
        line.strip()
        for line in (_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert ".env.*" in rules
    assert "!.env.example" in rules
    assert rules.index("!.env.example") > rules.index(".env.*")
