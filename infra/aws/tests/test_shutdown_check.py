"""Offline fail-closed checks for data-preserving pause plans; no AWS clients."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "check-project-shutdown.py"
SPEC = importlib.util.spec_from_file_location("shutdown_check", SCRIPT)
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def plan_fixture():
    resources = []
    def add(kind, name, values, mode="managed"):
        resources.append({"address": f"{kind}.{name}", "type": kind, "mode": mode, "values": values})
    add("aws_caller_identity", "current", {"account_id": AUDIT.EXPECTED_ACCOUNT}, "data")
    for name in sorted(AUDIT.ZIP_FUNCTIONS):
        add("aws_lambda_function", f'zip["{name}"]', {"function_name": f"{AUDIT.PREFIX}-{name}", "reserved_concurrent_executions": 0,
            "arn": f"arn:aws:lambda:{AUDIT.EXPECTED_REGION}:{AUDIT.EXPECTED_ACCOUNT}:function:{AUDIT.PREFIX}-{name}"})
    for name in ["outbox", "cleanup"]:
        add("aws_cloudwatch_event_rule", f'maintenance["{name}"]', {"name": f"{AUDIT.PREFIX}-{name}", "state": "DISABLED"})
    for name in ["infer", "delete", "notify"]:
        add("aws_lambda_event_source_mapping", f'zip_queues["{name}"]', {"enabled": False})
    add("aws_apigatewayv2_api", "app[0]", {"name": f"{AUDIT.PREFIX}-api", "disable_execute_api_endpoint": True})
    add("aws_cognito_user_pool", "users", {"admin_create_user_config": [{"allow_admin_create_user_only": True}]})
    bucket = f"{AUDIT.PREFIX}-{AUDIT.EXPECTED_ACCOUNT}-media"
    policy = {"Statement": [{"Sid": "DenyDataAccessWhileRuntimePaused", "Effect": "Deny", "Principal": "*",
        "Action": ["s3:GetObject*", "s3:PutObject*", "s3:DeleteObject*", "s3:ListBucket"],
        "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"]}]}
    add("aws_s3_bucket_policy", "media_tls", {"bucket": bucket, "policy": json.dumps(policy)})
    return {"planned_values": {"root_module": {"resources": resources}},
            "resource_changes": [{"address": r["address"], "type": r["type"], "mode": r["mode"], "change": {"actions": ["update"]}}
                                 for r in resources if r["mode"] == "managed"]}


def passes(plan):
    return all(c["passed"] for c in AUDIT.inspect_plan(plan)["checks"])


class ShutdownPlanChecks(unittest.TestCase):
    def test_api_custom_domain_mappings_read_every_page_without_generated_paginator(self):
        from botocore.session import Session
        client = Mock()
        client.meta.service_model = Session().get_service_model("apigatewayv2")
        client.get_api_mappings.side_effect = [
            {"Items": [{"ApiId": "another-api"}], "NextToken": "second-page"},
            {"Items": [{"ApiId": "target-project-api"}]},
        ]
        pages = list(AUDIT.api_mapping_pages(client, "fixture.example.invalid"))
        self.assertEqual([m["ApiId"] for p in pages for m in p["Items"]], ["another-api", "target-project-api"])
        self.assertEqual(client.get_api_mappings.call_args_list[1].kwargs,
                         {"DomainName": "fixture.example.invalid", "NextToken": "second-page"})

    def test_valid_pause_is_configuration_only_not_cloud_or_billing_acceptance(self):
        report = AUDIT.inspect_plan(plan_fixture())
        self.assertTrue(all(c["passed"] for c in report["checks"]))
        self.assertFalse(report["cloudVerified"])
        self.assertFalse(report["cognitoExistingUsersVerifiedDisabled"])

    def test_terraform_identity_in_refreshed_prior_state_is_validated(self):
        plan = plan_fixture()
        resources = plan["planned_values"]["root_module"]["resources"]
        identity = resources.pop(0)
        plan["prior_state"] = {"values": {"root_module": {"resources": [identity]}}}
        self.assertTrue(passes(plan))
        identity["values"]["account_id"] = "000000000000"
        self.assertFalse(passes(plan))

    def test_resource_creation_deletion_and_replacement_each_fail(self):
        for actions in [["create"], ["delete"], ["delete", "create"]]:
            with self.subTest(actions=actions):
                plan = plan_fixture()
                plan["resource_changes"][0]["change"]["actions"] = actions
                self.assertFalse(passes(plan))

    def test_other_project_and_non_runtime_updates_fail(self):
        for kind, address in [("aws_lambda_function", "aws_lambda_function.other"), ("aws_dynamodb_table", 'aws_dynamodb_table.data["media"]')]:
            with self.subTest(address=address):
                plan = plan_fixture()
                plan["resource_changes"].append({"address": address, "type": kind, "mode": "managed", "change": {"actions": ["update"]}})
                self.assertFalse(passes(plan))

    def test_wrong_account_or_region_fails(self):
        for account, region in [("000000000000", AUDIT.EXPECTED_REGION), (AUDIT.EXPECTED_ACCOUNT, "ap-southeast-2")]:
            with self.subTest(account=account, region=region):
                plan = plan_fixture()
                plan["planned_values"]["root_module"]["resources"][0]["values"]["account_id"] = account
                plan["planned_values"]["root_module"]["resources"][1]["values"]["arn"] = f"arn:aws:lambda:{region}:{account}:function:{AUDIT.PREFIX}-api"
                self.assertFalse(passes(plan))

    def test_each_required_pause_control_must_be_present(self):
        changes = [("aws_lambda_function", "reserved_concurrent_executions", -1),
                   ("aws_cloudwatch_event_rule", "state", "ENABLED"),
                   ("aws_lambda_event_source_mapping", "enabled", True),
                   ("aws_apigatewayv2_api", "disable_execute_api_endpoint", False),
                   ("aws_cognito_user_pool", "admin_create_user_config", [{"allow_admin_create_user_only": False}])]
        for kind, key, value in changes:
            with self.subTest(kind=kind):
                plan = plan_fixture()
                row = next(r for r in plan["planned_values"]["root_module"]["resources"] if r["type"] == kind)
                row["values"][key] = value
                self.assertFalse(passes(plan))

    def test_s3_policy_requires_unconditional_current_bucket_deny_and_management(self):
        plan = plan_fixture()
        row = next(r for r in plan["planned_values"]["root_module"]["resources"] if r["type"] == "aws_s3_bucket_policy")
        original = json.loads(row["values"]["policy"])
        for change in ["allow", "conditional", "wrong_bucket", "missing_put", "lock_admin"]:
            with self.subTest(change=change):
                policy = copy.deepcopy(original)
                statement = policy["Statement"][0]
                if change == "allow": statement["Effect"] = "Allow"
                if change == "conditional": statement["Condition"] = {"NumericGreaterThan": {"s3:signatureAge": 900000}}
                if change == "wrong_bucket": statement["Resource"] = ["arn:aws:s3:::other/*"]
                if change == "missing_put": statement["Action"].remove("s3:PutObject*")
                if change == "lock_admin": policy["Statement"].append({"Effect": "Deny", "Principal": "*", "Action": "s3:*", "Resource": "*"})
                row["values"]["policy"] = json.dumps(policy)
                self.assertFalse(passes(plan))

    def test_existing_s3_destination_must_be_suspended(self):
        plan = plan_fixture()
        plan["planned_values"]["root_module"]["resources"].append({"type": "aws_s3_bucket_notification", "values": {"queue": [{"queue_arn": "fixture"}]}})
        self.assertFalse(passes(plan))

    def test_cli_offline_report_omits_private_plan_values_and_returns_failure(self):
        tmp_root = ROOT / "tmp"
        tmp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=tmp_root) as folder:
            folder = Path(folder)
            document = plan_fixture()
            document["planned_values"]["root_module"]["resources"][1]["values"]["environment"] = {"PRIVATE_TEST_FIELD": "audit-fixture-not-a-secret"}
            input_path, output = folder / "input.json", folder / "report.json"
            input_path.write_text(json.dumps(document), encoding="utf-8")
            command = [sys.executable, str(SCRIPT), "--plan-json", str(input_path), "--output", str(output)]
            passed = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(passed.returncode, 0, passed.stderr)
            self.assertNotIn("audit-fixture-not-a-secret", output.read_text() + passed.stdout + passed.stderr)
            document["resource_changes"][0]["change"]["actions"] = ["delete"]
            input_path.write_text(json.dumps(document), encoding="utf-8")
            failed = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(failed.returncode, 1)
            input_path.write_text("invalid-json", encoding="utf-8")
            blocked = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(blocked.returncode, 2)


if __name__ == "__main__":
    unittest.main()
