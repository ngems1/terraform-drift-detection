"""Messages (Slack, Teams, Markdown, text) and delivery (retries, https, no URL leaks)."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest
from conftest import MOTO_IGNORES, real_plan

from driftwatch import config, cost, notify, render
from driftwatch.plan import PendingChange, PlanError, StackReport, analyse, error_report

CTX = render.Context(run_url="https://github.com/o/r/actions/runs/1")


def reports() -> list[StackReport]:
    drift = analyse("app-storage/prod", real_plan("drift"), MOTO_IGNORES)
    drift.monthly_cost_delta, drift.currency = "-12.40", "USD"
    return [
        StackReport("network/prod", "clean"),
        drift,
        error_report("billing/prod", "terraform init failed: AccessDenied"),
        StackReport("app-storage/dev", "pending", pending=[PendingChange("aws_sqs_queue.jobs", "update", False)]),
    ]


class TestRender:
    def test_headline_names_the_worst_status_and_counts(self) -> None:
        assert render.headline(reports(), CTX) == (
            "🚨 Terraform drift: drift check FAILED (1 failed, 1 drifted, 1 with unapplied changes)"
        )
        assert render.headline([StackReport("a", "clean")], CTX) == "✅ Terraform drift: no drift (1 stack(s) checked)"

    def test_text_orders_stacks_worst_first(self) -> None:
        lines = render.text(reports(), CTX).splitlines()
        stacks = [line.split()[1].rstrip(":") for line in lines if line[:1] in "🚨🔶🕓✅" and ": " in line[2:]]
        assert stacks[1:] == ["billing/prod", "app-storage/prod", "app-storage/dev", "network/prod"]
        text = "\n".join(lines)
        assert "visibility_timeout_seconds 60 → 300" in text
        assert "terraform apply would change the monthly cost by -12.40 USD" in text
        assert "drift in this stack is NOT being detected" in text
        assert "Unapplied code changes: aws_sqs_queue.jobs (update)" in text
        assert text.rstrip().endswith("run: https://github.com/o/r/actions/runs/1")

    def test_slack_payload(self) -> None:
        payload = render.slack(reports(), CTX)
        blocks = payload["blocks"]
        assert payload["text"].startswith("🚨")
        assert blocks[0]["type"] == "header"
        sections = [b["text"]["text"] for b in blocks if b["type"] == "section"]
        assert len(sections) == 3  # clean stacks don't get a section
        assert sections[0].startswith("*🚨 billing/prod*")
        assert blocks[-1]["elements"][0]["text"] == "<https://github.com/o/r/actions/runs/1|Workflow run>"
        assert any("✅ clean: network/prod" in b["elements"][0]["text"] for b in blocks if b["type"] == "context")

    def test_slack_escapes_angle_brackets(self) -> None:
        r = error_report("s", "unexpected <token> & more")
        section = render.slack([r], CTX)["blocks"][1]["text"]["text"]
        assert "&lt;token&gt; &amp; more" in section

    def test_slack_caps_the_number_of_blocks(self) -> None:
        many = [error_report(f"s{i}", "x") for i in range(40)]
        blocks = render.slack(many, CTX)["blocks"]
        assert len(blocks) <= 50
        assert any("and 25 more stack(s)" in b["elements"][0]["text"] for b in blocks if b["type"] == "context")

    def test_teams_adaptive_card(self) -> None:
        payload = render.teams(reports(), CTX)
        (attachment,) = payload["attachments"]
        card = attachment["content"]
        assert payload["type"] == "message"
        assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
        assert card["type"] == "AdaptiveCard"
        assert card["actions"][0]["url"] == CTX.run_url
        texts = [b["text"] for b in card["body"]]
        assert any("aws_sqs_queue.jobs: visibility_timeout_seconds 60 → 300" in t for t in texts)

    def test_markdown(self) -> None:
        md = render.markdown(reports(), CTX)
        assert md.startswith("### 🚨")
        assert "**🔶 `app-storage/prod`: drift**" in md

    def test_long_lists_are_capped(self) -> None:
        r = StackReport(
            "s", "pending", pending=[PendingChange(f"aws_sqs_queue.q{i}", "create", False) for i in range(12)]
        )
        assert "and 4 more" in render.text([r], CTX)


class Handler(BaseHTTPRequestHandler):
    script: ClassVar[list[tuple[int, dict[str, str]]]] = []
    seen: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Handler.seen.append(json.loads(body))
        status, headers = Handler.script.pop(0) if Handler.script else (200, {})
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def server() -> Iterator[str]:
    Handler.script, Handler.seen = [], []
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/hook/SECRET-PATH"
    httpd.shutdown()


class TestNotify:
    def test_delivers_utf8_json(self, server: str) -> None:
        assert notify.post(server, {"text": "60 → 300"}, channel="slack") == 200
        assert Handler.seen == [{"text": "60 → 300"}]

    def test_retries_429_honouring_retry_after_then_succeeds(self, server: str) -> None:
        Handler.script = [(429, {"Retry-After": "7"}), (503, {}), (200, {})]
        sleeps: list[float] = []
        assert notify.post(server, {"t": 1}, channel="slack", sleep=sleeps.append) == 200
        assert sleeps == [7.0, 2.0]
        assert len(Handler.seen) == 3

    def test_gives_up_and_never_prints_the_url(self, server: str) -> None:
        Handler.script = [(500, {})] * 4
        with pytest.raises(notify.DeliveryError) as exc:
            notify.post(server, {}, channel="teams", sleep=lambda _s: None)
        assert "after 4 attempts (HTTP 500)" in str(exc.value)
        assert "SECRET-PATH" not in str(exc.value)

    def test_client_errors_are_not_retried(self, server: str) -> None:
        Handler.script = [(404, {})]
        with pytest.raises(notify.DeliveryError, match="rejected the message"):
            notify.post(server, {}, channel="slack", sleep=lambda _s: None)
        assert len(Handler.seen) == 1

    def test_network_errors_are_retried(self) -> None:
        sleeps: list[float] = []
        with pytest.raises(notify.DeliveryError, match="network error"):
            notify.post("http://127.0.0.1:9/x", {}, channel="slack", attempts=2, timeout=1, sleep=sleeps.append)
        assert sleeps == [1.0]

    def test_plain_http_is_refused(self) -> None:
        with pytest.raises(notify.DeliveryError, match="must use https"):
            notify.post("http://hooks.example.com/x", {}, channel="slack")

    def test_bad_retry_after_falls_back_to_backoff(self) -> None:
        assert notify._retry_after("soon", 3) == 4.0
        assert notify._retry_after("600", 1) == 60.0


class TestConfigAndCost:
    def test_config(self, tmp_path: Path) -> None:
        p = tmp_path / "drift.toml"
        p.write_text('[[ignore]]\naddress = "a.*"\nattributes = ["x"]\nreason = "r"\n', encoding="utf-8")
        assert config.load(p)[0].attributes == ("x",)
        assert config.load(None) == []

    @pytest.mark.parametrize(
        ("text", "error"),
        [
            ("ignores = []", "unknown key"),
            ('[[ignore]]\naddress = "a"', "`reason` is required"),
            ('[[ignore]]\nreason = "r"', "`address` is required"),
            ('[[ignore]]\naddress = "a"\nreason = "r"\nowner = "me"', "unknown key"),
            ('[[ignore]]\naddress = "a"\nreason = "r"\nattributes = "x"', "list of strings"),
            ("ignore = [1]", "expected a table"),
            ("[[ignore]", "invalid TOML"),
        ],
    )
    def test_config_errors(self, tmp_path: Path, text: str, error: str) -> None:
        p = tmp_path / "drift.toml"
        p.write_text(text, encoding="utf-8")
        with pytest.raises(PlanError, match=error):
            config.load(p)

    def test_config_missing(self, tmp_path: Path) -> None:
        with pytest.raises(PlanError, match="cannot read"):
            config.load(tmp_path / "nope.toml")

    def test_cost_delta(self, tmp_path: Path) -> None:
        p = tmp_path / "c.json"
        p.write_text(
            json.dumps(
                {"projects": [], "currency": "EUR", "totalMonthlyCost": "10.5", "pastTotalMonthlyCost": "30.25"}
            ),
            encoding="utf-8",
        )
        assert cost.monthly_delta(p) == ("-19.75", "EUR")
        p.write_text(json.dumps({"projects": [], "totalMonthlyCost": None}), encoding="utf-8")
        assert cost.monthly_delta(p) == (None, "USD")

    @pytest.mark.parametrize(
        ("text", "error"),
        [
            ("{", "not valid JSON"),
            ("{}", "not Infracost"),
            ('{"projects": [], "totalMonthlyCost": "x", "pastTotalMonthlyCost": "1"}', "invalid amount"),
        ],
    )
    def test_cost_errors(self, tmp_path: Path, text: str, error: str) -> None:
        p = tmp_path / "c.json"
        p.write_text(text, encoding="utf-8")
        with pytest.raises(PlanError, match=error):
            cost.monthly_delta(p)
        with pytest.raises(PlanError, match="cannot read"):
            cost.monthly_delta(tmp_path / "missing.json")
