"""The security engine offline: parsers on recorded reports, the expiring allow-list, the verdict.

The reports under ``tests/checks/fixtures`` were recorded from real gitleaks 8.30.1,
bandit 1.9.4 and pip-audit 2.10.1 runs (trimmed; the secret is redacted by gitleaks).
No tool is started here.
"""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

import pytest

from checks._sink import MemorySink
from checks.security import scan as scan_module
from checks.security import tools
from checks.security.allowlist import AllowEntry, Allowlist
from checks.security.model import SecurityFinding, Severity, ToolRun
from checks.security.scan import scan

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TODAY = date(2026, 10, 16)


def _fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _finding(fp="bandit:B1:a.py:abc", tool="bandit") -> SecurityFinding:
    return SecurityFinding(tool, "B1", Severity.MEDIUM, "a.py:1", fp)


class TestParsers:
    def test_gitleaks(self):
        (leak,) = tools.parse_gitleaks(_fixture("gitleaks-report.json"))
        assert leak.rule_id == "generic-api-key" and leak.severity is Severity.HIGH
        assert leak.location.startswith("tests/tools/test_ci_reader.py:20@")
        assert leak.fingerprint == "gitleaks:generic-api-key:tests/tools/test_ci_reader.py:20"

    def test_bandit_severity_and_stable_fingerprint(self):
        first, second = tools.parse_bandit(_fixture("bandit-report.json"))
        assert (first.rule_id, first.severity) == ("B324", Severity.HIGH)
        assert (second.rule_id, second.severity) == ("B608", Severity.MEDIUM)
        assert first.location == "autonomous_ui/locator_store.py:84"
        assert first.fingerprint.startswith("bandit:B324:autonomous_ui/locator_store.py:")

    def test_bandit_fingerprint_survives_a_line_shift(self):
        raw = _fixture("bandit-report.json")
        before = tools.parse_bandit(raw)[0].fingerprint
        result = raw["results"][0]
        result["code"] = "\n".join(
            f"{int(c.split(' ', 1)[0]) + 5} {c.split(' ', 1)[1]}"
            for c in result["code"].splitlines()
        )
        result["line_number"] += 5
        assert tools.parse_bandit(raw)[0].fingerprint == before

    def test_pip_audit_vulnerability_with_a_fix(self):
        (vuln,) = tools.parse_pip_audit(_fixture("pip-audit-report.json"))
        assert vuln.location == "idna==3.11" and vuln.rule_id == "PYSEC-2026-215"
        assert vuln.fix == "upgrade to 3.15"

    @pytest.mark.parametrize(
        ("parser", "raw"),
        [
            (tools.parse_gitleaks, {}),
            (tools.parse_bandit, []),
            (tools.parse_bandit, {"results": "x"}),
            (tools.parse_pip_audit, {"deps": []}),
        ],
    )
    def test_garbage_is_refused(self, parser, raw):
        with pytest.raises(ValueError):
            parser(raw)


class TestAllowlist:
    def _write(self, tmp_path, body: str) -> Path:
        path = tmp_path / "allowlist.yml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_a_matching_fingerprint_is_exempt(self):
        allow = Allowlist([AllowEntry("bandit:B1:a.py:abc", "ok", date(2027, 1, 1))])
        judged = allow.apply([_finding()], TODAY)
        assert judged.blocking == [] and len(judged.allowlisted) == 1

    def test_an_expired_entry_exempts_nothing_and_is_reported(self):
        allow = Allowlist([AllowEntry("bandit:B1:a.py:abc", "ok", date(2026, 10, 15))])
        judged = allow.apply([_finding()], TODAY)
        assert len(judged.blocking) == 1 and len(judged.expired) == 1

    def test_the_expiry_day_itself_still_counts(self):
        allow = Allowlist([AllowEntry("bandit:B1:a.py:abc", "ok", TODAY)])
        assert allow.apply([_finding()], TODAY).blocking == []

    def test_unused_entries_are_listed(self):
        allow = Allowlist([AllowEntry("gone", "fixed long ago", date(2027, 1, 1))])
        assert [e.fingerprint for e in allow.apply([], TODAY).unused] == ["gone"]

    @pytest.mark.parametrize(
        "body",
        [
            "entries:\n  - fingerprint: x\n    expires: 2027-01-01\n",
            "entries:\n  - fingerprint: x\n    reason: r\n",
            "entries:\n  - reason: r\n    expires: 2027-01-01\n",
            "entries:\n  - fingerprint: x\n    reason: r\n    expires: someday\n",
            "entries: nope\n",
            "entries:\n  - {fingerprint: x, reason: r, expires: 2027-01-01}\n"
            "  - {fingerprint: x, reason: r, expires: 2027-01-01}\n",
        ],
    )
    def test_incomplete_entries_are_rejected_at_load(self, tmp_path, body):
        with pytest.raises(ValueError):
            Allowlist.load(self._write(tmp_path, body))

    def test_load(self, tmp_path):
        path = self._write(
            tmp_path, 'entries:\n  - fingerprint: "a"\n    reason: "b"\n    expires: 2027-01-01\n'
        )
        assert Allowlist.load(path).entries == [AllowEntry("a", "b", date(2027, 1, 1))]

    def test_the_committed_allowlist_is_valid(self):
        entries = Allowlist.load(Path(__file__).resolve().parents[3] / "security" / "allowlist.yml")
        assert entries.entries and all(e.reason for e in entries.entries)


def _runner(results: dict[str, ToolRun]):
    def run(name, **kwargs):  # noqa: ANN001, ANN003, ARG001
        return results[name]

    return run


class TestVerdict:
    def test_every_tool_ran_and_nothing_blocks_is_pass(self, tmp_path):
        runs = {t: ToolRun(t, ran=True) for t in ("gitleaks", "bandit", "pip-audit")}
        out = scan(allowlist=Allowlist(), runner=_runner(runs), today=TODAY, out_dir=tmp_path)
        assert out.status == "pass"

    def test_a_cve_fails(self, tmp_path):
        """The old plugin read pip-audit's output and never let it change the status."""
        vuln = tools.parse_pip_audit(_fixture("pip-audit-report.json"))
        runs = {"pip-audit": ToolRun("pip-audit", ran=True, findings=vuln)}
        out = scan(["pip-audit"], allowlist=Allowlist(), runner=_runner(runs), today=TODAY)
        assert out.status == "fail" and "pip-audit" in out.reason

    def test_a_missing_tool_is_unknown(self):
        runs = {
            "bandit": ToolRun("bandit", ran=True),
            "gitleaks": ToolRun("gitleaks", ran=False, reason="gitleaks is not installed"),
        }
        out = scan(["bandit", "gitleaks"], allowlist=Allowlist(), runner=_runner(runs), today=TODAY)
        assert out.status == "unknown" and "gitleaks is not installed" in out.reason

    def test_a_blocking_finding_fails_even_when_another_tool_is_missing(self):
        runs = {
            "bandit": ToolRun("bandit", ran=True, findings=[_finding()]),
            "gitleaks": ToolRun("gitleaks", ran=False, reason="absent"),
        }
        out = scan(["bandit", "gitleaks"], allowlist=Allowlist(), runner=_runner(runs), today=TODAY)
        assert out.status == "fail" and "did not run" in out.reason

    def test_an_expired_entry_fails_on_its_own(self):
        runs = {"bandit": ToolRun("bandit", ran=True)}
        allow = Allowlist([AllowEntry("old", "was fine", date(2026, 1, 1))])
        out = scan(["bandit"], allowlist=allow, runner=_runner(runs), today=TODAY)
        assert out.status == "fail" and "expired" in out.reason

    def test_allowlisted_findings_pass_and_are_recorded(self):
        sink = MemorySink()
        runs = {"bandit": ToolRun("bandit", ran=True, version="1.9.4", findings=[_finding()])}
        allow = Allowlist([AllowEntry("bandit:B1:a.py:abc", "ok", date(2027, 1, 1))])
        out = scan(["bandit"], allowlist=allow, runner=_runner(runs), today=TODAY, sink=sink)
        assert out.status == "pass"
        row = sink.rows["metric_security_finding"][0]
        assert row["allowlisted"] == 1 and row["allowlist_expires"] == "2027-01-01"
        assert sink.rows["metric_security_scan"][0]["blocking"] == 0

    def test_an_unknown_tool_name_is_unknown(self, tmp_path):
        out = scan(["zap"], allowlist=Allowlist(), today=TODAY, out_dir=tmp_path)
        assert out.status == "unknown"

    def test_nothing_requested_is_unknown(self):
        assert scan([], allowlist=Allowlist()).status == "unknown"


class TestRunnersWithoutTools:
    def test_missing_gitleaks(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GITLEAKS_BIN", raising=False)
        monkeypatch.setattr(tools.shutil, "which", lambda name: None)
        run = tools.run_gitleaks(tmp_path, report=tmp_path / "r.json")
        assert not run.ran and "not installed" in run.reason

    def test_missing_python_tools(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tools, "_module_cli", lambda module: None)
        assert not tools.run_bandit([tmp_path], report=tmp_path / "b.json").ran
        lock = tmp_path / "lock.txt"
        lock.write_text("x==1\n")
        assert not tools.run_pip_audit(lock, report=tmp_path / "p.json").ran

    def test_a_tool_that_writes_no_report_did_not_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tools, "_module_cli", lambda module: ["python", "-m", module])
        monkeypatch.setattr(
            tools,
            "_run",
            lambda cmd, cwd=None: subprocess.CompletedProcess(cmd, 1, "", "network down"),
        )
        monkeypatch.setattr(tools, "_version", lambda cmd: None)
        lock = tmp_path / "lock.txt"
        lock.write_text("x==1\n")
        run = tools.run_pip_audit(lock, report=tmp_path / "p.json")
        assert not run.ran and "network down" in run.reason

    def test_pip_audit_reads_every_lock_file_and_names_a_missing_one(self, tmp_path, monkeypatch):
        seen = {}

        def run(cmd, cwd=None):
            seen["cmd"] = cmd
            Path(cmd[cmd.index("-o") + 1]).write_text('{"dependencies": []}')
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(tools, "_module_cli", lambda module: ["python", "-m", module])
        monkeypatch.setattr(tools, "_run", run)
        monkeypatch.setattr(tools, "_version", lambda cmd: None)
        first, second = tmp_path / "a.txt", tmp_path / "b.txt"
        first.write_text("x==1\n")
        assert "b.txt" in tools.run_pip_audit([first, second], report=tmp_path / "p.json").reason
        second.write_text("y==2\n")
        assert tools.run_pip_audit([first, second], report=tmp_path / "p.json").ran
        assert seen["cmd"][3:7] == ["-r", str(first), "-r", str(second)]

    def test_every_requirements_file_is_audited_and_fully_pinned(self):
        """A requirements file left out of the audit is invisible to the security gate."""
        audited = {p.name for p in scan_module.DEFAULT_LOCKFILES}
        present = {p.name for p in scan_module.REPO_ROOT.glob("requirements*.txt")}
        assert present - audited == {"requirements.txt"}
        for path in scan_module.DEFAULT_LOCKFILES:
            lines = path.read_text(encoding="utf-8").splitlines()
            loose = [ln for ln in lines if ln.split("#")[0].strip() and "==" not in ln]
            assert not loose, f"{path.name}: pip-audit --no-deps needs exact pins: {loose}"

    def test_default_bandit_targets_skip_tests_and_venv(self, tmp_path):
        for name in ("pkg", "tests", ".venv", "docs"):
            (tmp_path / name).mkdir()
        (tmp_path / "pkg" / "__init__.py").write_text("")
        (tmp_path / "tests" / "__init__.py").write_text("")
        assert tools.default_bandit_targets(tmp_path) == [tmp_path / "pkg"]
