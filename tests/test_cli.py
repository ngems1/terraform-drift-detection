"""The CLI contract: reports are always written; notify posts only when needed; exit codes."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import PLANS, ROOT

from driftwatch.cli import EXIT_ERROR, EXIT_FAIL_ON, EXIT_OK, main

MOTO_CONFIG = ROOT / "tests" / "e2e" / "drift.toml"


def run(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    return main(list(args), stdout=out, stderr=err), out.getvalue(), err.getvalue()


def report(tmp_path: Path, name: str, *extra: str) -> Path:
    out = tmp_path / f"{name}.json"
    code, _, _ = run("report", "--stack", f"app/{name}", *extra, "--out", str(out))
    assert code == EXIT_OK
    return out


class TestReport:
    @pytest.mark.parametrize(("plan", "status"), [("clean", "clean"), ("drift", "drift"), ("pending", "pending")])
    def test_real_plans(self, tmp_path: Path, plan: str, status: str) -> None:
        out = report(tmp_path, plan, "--plan", str(PLANS / f"{plan}.json"), "--config", str(MOTO_CONFIG))
        assert json.loads(out.read_text(encoding="utf-8"))["status"] == status

    def test_error_flag(self, tmp_path: Path) -> None:
        out = report(tmp_path, "e", "--error", "terraform plan failed (exit 1)")
        assert json.loads(out.read_text(encoding="utf-8"))["error"] == "terraform plan failed (exit 1)"

    def test_broken_input_becomes_an_error_report_never_clean(self, tmp_path: Path) -> None:
        out = tmp_path / "r.json"
        code, _, err = run("report", "--stack", "s", "--plan", str(tmp_path / "missing.json"), "--out", str(out))
        assert code == EXIT_OK
        assert json.loads(out.read_text(encoding="utf-8"))["status"] == "error"
        assert "cannot read plan file" in err

    def test_with_infracost(self, tmp_path: Path) -> None:
        c = tmp_path / "cost.json"
        c.write_text('{"projects": [], "totalMonthlyCost": "5", "pastTotalMonthlyCost": "8"}', encoding="utf-8")
        out = report(
            tmp_path, "d", "--plan", str(PLANS / "drift.json"), "--config", str(MOTO_CONFIG), "--infracost", str(c)
        )
        assert json.loads(out.read_text(encoding="utf-8"))["monthly_cost_delta"] == "-3.00"

    def test_cannot_write(self, tmp_path: Path) -> None:
        blocker = tmp_path / "file"
        blocker.write_text("", encoding="utf-8")
        code, _, err = run("report", "--stack", "s", "--error", "x", "--out", str(blocker / "r.json"))
        assert code == EXIT_ERROR
        assert "cannot write" in err


class TestNotify:
    def test_clean_posts_nothing(self, tmp_path: Path) -> None:
        r = report(tmp_path, "clean", "--plan", str(PLANS / "clean.json"), "--config", str(MOTO_CONFIG))
        code, out, _ = run("notify", str(r), "--slack-webhook-env", "NOT_SET")
        assert code == EXIT_OK
        assert "no message sent" in out

    def test_dry_run_prints_both_payloads(self, tmp_path: Path) -> None:
        r = report(tmp_path, "drift", "--plan", str(PLANS / "drift.json"), "--config", str(MOTO_CONFIG))
        code, out, _ = run("notify", str(r), "--slack-webhook-env", "S", "--teams-webhook-env", "T", "--dry-run")
        assert code == EXIT_OK  # default --fail-on error: drift alone doesn't fail the job
        assert "--- slack payload (dry run)" in out
        assert "application/vnd.microsoft.card.adaptive" in out

    @pytest.mark.parametrize(
        ("fail_on", "expected"),
        [("drift", EXIT_FAIL_ON), ("pending", EXIT_FAIL_ON), ("error", EXIT_OK), ("never", EXIT_OK)],
    )
    def test_fail_on(self, tmp_path: Path, fail_on: str, expected: int) -> None:
        r = report(tmp_path, "drift", "--plan", str(PLANS / "drift.json"), "--config", str(MOTO_CONFIG))
        code, _, _ = run("notify", str(r), "--slack-webhook-env", "S", "--dry-run", "--fail-on", fail_on)
        assert code == expected

    def test_error_reports_fail_by_default_and_heartbeat_markdown(self, tmp_path: Path) -> None:
        r = report(tmp_path, "e", "--error", "boom")
        md = tmp_path / "summary.md"
        code, _, _ = run("notify", str(r), "--slack-webhook-env", "S", "--dry-run", "--markdown", str(md))
        assert code == EXIT_FAIL_ON
        assert md.read_text(encoding="utf-8").startswith("### 🚨")

    def test_missing_channel_or_empty_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        r = report(tmp_path, "e", "--error", "boom")
        assert run("notify", str(r))[0] == EXIT_ERROR
        monkeypatch.delenv("EMPTY_HOOK", raising=False)
        code, _, err = run("notify", str(r), "--slack-webhook-env", "EMPTY_HOOK")
        assert code == EXIT_ERROR
        assert "EMPTY_HOOK is empty" in err

    def test_delivery_failure_is_exit_3(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        r = report(tmp_path, "e", "--error", "boom")
        monkeypatch.setenv("HOOK", "http://hooks.example.com/plain-http")
        code, _, err = run("notify", str(r), "--teams-webhook-env", "HOOK")
        assert code == EXIT_ERROR
        assert "must use https" in err

    def test_unreadable_reports(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{}", encoding="utf-8")
        assert run("notify", str(bad), "--slack-webhook-env", "S")[0] == EXIT_ERROR

    def test_heartbeat_when_clean(self, tmp_path: Path) -> None:
        r = report(tmp_path, "clean", "--plan", str(PLANS / "clean.json"), "--config", str(MOTO_CONFIG))
        code, out, _ = run("notify", str(r), "--slack-webhook-env", "S", "--dry-run", "--heartbeat")
        assert code == EXIT_OK
        assert "no drift" in out


def test_python_dash_m() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "driftwatch", "--version"],
        capture_output=True,
        text=True,
        check=False,
        env={"PYTHONPATH": str(ROOT / "src")},
    )
    assert proc.stdout.startswith("driftwatch ")
