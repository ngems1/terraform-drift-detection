"""Drift vs pending vs noise, on real plans (Moto e2e) and synthetic ones."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import MOTO_IGNORES, entry, plan, real_plan

from driftwatch.plan import Ignore, PlanError, StackReport, analyse, error_report, load


class TestRealPlans:
    def test_clean_plan_after_apply(self) -> None:
        report = analyse("s", real_plan("clean"), MOTO_IGNORES)
        assert report.status == "clean"
        assert report.drift == []
        assert report.pending == []
        assert report.terraform_version.startswith("1.")

    def test_without_the_ignores_the_emulator_quirks_show_as_pending(self) -> None:
        # Perpetual diffs are real: here Moto's; in production, a provider's. Nothing drifted.
        report = analyse("s", real_plan("clean"))
        assert report.status == "pending"
        assert report.drift == []
        assert {p.address for p in report.pending} == {"aws_s3_bucket.uploads", "aws_sqs_queue.jobs"}

    def test_drift_after_out_of_band_changes(self) -> None:
        report = analyse("s", real_plan("drift"), MOTO_IGNORES)
        assert report.status == "drift"
        found = {d.address: {a.name: (a.before, a.after) for a in d.attributes} for d in report.drift}
        assert found["aws_sqs_queue.jobs"] == {"visibility_timeout_seconds": ("60", "300")}
        assert found["aws_s3_bucket.uploads"] == {"tags": ("(none)", '{"owner":"someone-in-the-console"}')}
        assert list(found["aws_s3_bucket_versioning.uploads"]) == ["versioning_configuration"]
        # every planned change here only reverts drift
        assert all(p.reverts_drift for p in report.pending)

    def test_harmless_refresh_differences_are_not_drift(self) -> None:
        # resource_drift in the clean plan lists the bucket's legacy versioning/SSE attributes
        # (managed by separate resources) and tags null -> {}; Terraform wouldn't change them.
        raw = real_plan("clean")
        assert any(d["address"] == "aws_s3_bucket.uploads" for d in raw["resource_drift"])
        assert analyse("s", raw, MOTO_IGNORES).drift == []

    def test_unapplied_code_is_pending_not_drift(self) -> None:
        report = analyse("s", real_plan("pending"), MOTO_IGNORES)
        assert report.status == "pending"
        assert report.drift == []
        assert [(p.address, p.reverts_drift) for p in report.pending] == [("aws_sqs_queue.jobs", False)]


class TestSynthetic:
    def test_resource_deleted_outside_terraform(self) -> None:
        p = plan(
            drift=[entry("aws_sqs_queue.q", {"name": "q"}, None, ["delete"])],
            changes=[entry("aws_sqs_queue.q", None, {"name": "q"}, ["create"])],
        )
        report = analyse("s", p)
        assert report.status == "drift"
        assert report.drift[0].action == "delete"
        assert report.pending[0].reverts_drift

    def test_sensitive_values_are_masked(self) -> None:
        p = plan(
            drift=[
                entry(
                    "aws_db_instance.db",
                    {"password": "old"},
                    {"password": "new"},
                    ["update"],
                    after_sensitive={"password": True},
                )
            ],
            changes=[
                entry(
                    "aws_db_instance.db",
                    {"password": "new"},
                    {"password": "old"},
                    ["update"],
                    after_sensitive={"password": True},
                )
            ],
        )
        (attr,) = analyse("s", p).drift[0].attributes
        assert (attr.before, attr.after) == ("(sensitive)", "(sensitive)")

    def test_long_values_are_shortened(self) -> None:
        long = "x" * 500
        p = plan(
            drift=[entry("aws_iam_policy.p", {"policy": "a"}, {"policy": long}, ["update"])],
            changes=[entry("aws_iam_policy.p", {"policy": long}, {"policy": "a"}, ["update"])],
        )
        (attr,) = analyse("s", p).drift[0].attributes
        assert attr.after.endswith("(500 chars)")
        assert len(attr.after) < 80

    def test_tags_all_is_shown_once(self) -> None:
        before, after = {"tags": {}, "tags_all": {"a": "1"}}, {"tags": {"x": "y"}, "tags_all": {"a": "1", "x": "y"}}
        p = plan(
            drift=[entry("aws_s3_bucket.b", before, after, ["update"])],
            changes=[entry("aws_s3_bucket.b", after, before, ["update"])],
        )
        assert [a.name for a in analyse("s", p).drift[0].attributes] == ["tags"]

    def test_a_tags_all_only_change_is_still_pending(self) -> None:
        # e.g. the provider's default_tags changed in code: only tags_all moves.
        p = plan(changes=[entry("aws_s3_bucket.b", {"tags_all": {}}, {"tags_all": {"team": "a"}}, ["update"])])
        assert analyse("s", p).status == "pending"

    def test_resource_level_ignore(self) -> None:
        p = plan(
            drift=[entry("aws_autoscaling_group.g", {"desired_capacity": 2}, {"desired_capacity": 5}, ["update"])],
            changes=[entry("aws_autoscaling_group.g", {"desired_capacity": 5}, {"desired_capacity": 2}, ["update"])],
        )
        report = analyse("s", p, [Ignore("aws_autoscaling_group.*", (), "the autoscaler owns it")])
        assert report.status == "clean"
        assert report.ignored == ["aws_autoscaling_group.g"]

    def test_ignored_attribute_keeps_other_attributes(self) -> None:
        before = {"desired_capacity": 2, "max_size": 4}
        after = {"desired_capacity": 5, "max_size": 9}
        p = plan(
            drift=[entry("aws_autoscaling_group.g", before, after, ["update"])],
            changes=[entry("aws_autoscaling_group.g", after, before, ["update"])],
        )
        report = analyse("s", p, [Ignore("aws_autoscaling_group.g", ("desired_capacity",), "autoscaler")])
        assert [a.name for a in report.drift[0].attributes] == ["max_size"]
        assert report.ignored == ["aws_autoscaling_group.g desired_capacity"]

    def test_create_replace_and_data_sources(self) -> None:
        p = plan(
            changes=[
                entry("aws_sqs_queue.new", None, {"name": "n"}, ["create"]),
                entry("aws_instance.i", {"ami": "a"}, {"ami": "b"}, ["delete", "create"]),
                {**entry("data.aws_ami.x", None, {}, ["read"]), "mode": "data"},
                entry("aws_sqs_queue.same", {"name": "s"}, {"name": "s"}, ["no-op"]),
            ]
        )
        assert [(c.address, c.action) for c in analyse("s", p).pending] == [
            ("aws_sqs_queue.new", "create"),
            ("aws_instance.i", "replace"),
        ]

    def test_drift_on_a_resource_the_plan_leaves_alone_is_not_reported(self) -> None:
        p = plan(
            drift=[
                entry(
                    "aws_s3_bucket.b",
                    {"versioning": [{"enabled": False}]},
                    {"versioning": [{"enabled": True}]},
                    ["update"],
                )
            ]
        )
        assert analyse("s", p).status == "clean"


class TestLoadAndRoundTrip:
    def test_load_errors(self, tmp_path: Path) -> None:
        with pytest.raises(PlanError, match="cannot read"):
            load(tmp_path / "missing.json")
        binary = tmp_path / "tfplan"
        binary.write_bytes(b"PK\x03\x04")
        with pytest.raises(PlanError, match="binary plan"):
            load(binary)
        bad = tmp_path / "bad.json"
        bad.write_text("nope", encoding="utf-8")
        with pytest.raises(PlanError, match="not valid JSON"):
            load(bad)
        bad.write_text('{"format_version": "2.0"}', encoding="utf-8")
        with pytest.raises(PlanError, match="format_version 1"):
            load(bad)
        bad.write_text('{"format_version": "1.2", "errored": true}', encoding="utf-8")
        with pytest.raises(PlanError, match="errored"):
            load(bad)

    def test_report_round_trip(self) -> None:
        report = analyse("app/prod", real_plan("drift"), MOTO_IGNORES)
        again = StackReport.from_dict(json.loads(json.dumps(report.to_dict())))
        assert again == report

    def test_error_report_and_bad_schema(self) -> None:
        assert error_report("s", "boom").to_dict()["status"] == "error"
        with pytest.raises(PlanError, match="schema_version"):
            StackReport.from_dict({"schema_version": 2})
