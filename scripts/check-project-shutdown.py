"""Read-only, account-scoped runtime pause verification. Never applies a plan."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fnmatch
import json
from pathlib import Path
import sys

EXPECTED_ACCOUNT = "360493183467"
EXPECTED_REGION = "us-east-1"
PREFIX = "ecoguard3184-dev"
ZIP_FUNCTIONS = {"api", "infer", "delete", "notify", "outbox", "cleanup", "lex_fulfillment"}
ALLOWED_UPDATE_TYPES = {
    "aws_cloudwatch_event_rule", "aws_lambda_event_source_mapping",
    "aws_lambda_function", "aws_apigatewayv2_api", "aws_s3_bucket_policy",
    "aws_s3_bucket_notification", "aws_cognito_user_pool",
}
ALLOWED_UPDATE_ADDRESSES = {
    'aws_apigatewayv2_api.app[0]', 'aws_s3_bucket_policy.media_tls',
    'aws_s3_bucket_notification.uploads[0]', 'aws_cognito_user_pool.users',
    'aws_lambda_function.ingest[0]', 'aws_lambda_event_source_mapping.ingest[0]',
    *{f'aws_lambda_function.zip["{name}"]' for name in ZIP_FUNCTIONS},
    *{f'aws_lambda_event_source_mapping.zip_queues["{name}"]' for name in ["infer", "delete", "notify"]},
    *{f'aws_cloudwatch_event_rule.maintenance["{name}"]' for name in ["outbox", "cleanup"]},
}


def check(name, passed):
    return {"name": name, "passed": bool(passed)}


def list_value(value):
    return value if isinstance(value, list) else [value]


def paused_bucket_policy(policy, bucket):
    """Require an unconditional, exact-bucket deny; an allow/conditional deny fails."""
    if isinstance(policy, str):
        policy = json.loads(policy)
    resources = {f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"}
    required = {"s3:GetObject", "s3:GetObjectVersion", "s3:PutObject",
                "s3:DeleteObject", "s3:DeleteObjectVersion", "s3:ListBucket"}
    statements = list_value(policy.get("Statement", []))
    for statement in statements:
        if statement.get("Effect") == "Deny" and not statement.get("Condition"):
            if any(fnmatch.fnmatchcase(action, pattern)
                   for action in ["s3:GetBucketPolicy", "s3:PutBucketPolicy"]
                   for pattern in list_value(statement.get("Action", []))):
                return False
    for statement in statements:
        if statement.get("Sid") != "DenyDataAccessWhileRuntimePaused":
            continue
        actions = list_value(statement.get("Action", []))
        return (statement.get("Effect") == "Deny"
                and statement.get("Principal") == "*"
                and not statement.get("Condition")
                and set(list_value(statement.get("Resource", []))) == resources
                and all(any(fnmatch.fnmatchcase(action, pattern) for pattern in actions) for action in required)
                and not any(fnmatch.fnmatchcase("s3:PutBucketPolicy", pattern) for pattern in actions)
                and not any(fnmatch.fnmatchcase("s3:GetBucketPolicy", pattern) for pattern in actions))
    return False


def planned_resources(module):
    yield from module.get("resources", [])
    for child in module.get("child_modules", []):
        yield from planned_resources(child)


def api_mapping_pages(client, domain_name):
    # Botocore currently has no generated paginator for this paginated operation.
    # Use its Paginator machinery rather than silently inspecting only page one.
    from botocore.paginate import Paginator
    paginator = Paginator(client.get_api_mappings,
                          {"input_token": "NextToken", "output_token": "NextToken",
                           "result_key": "Items", "limit_key": "MaxResults"},
                          client.meta.service_model.operation_model("GetApiMappings"))
    return paginator.paginate(DomainName=domain_name)


def inspect_plan(document):
    changes = [r for r in document.get("resource_changes", []) if r.get("mode") == "managed"]
    destructive = [r["address"] for r in changes if any(a in {"create", "delete"} for a in r["change"]["actions"])]
    unexpected = [r["address"] for r in changes if r["change"]["actions"] != ["no-op"]
                  and (r["change"]["actions"] != ["update"] or r["type"] not in ALLOWED_UPDATE_TYPES
                       or r["address"] not in ALLOWED_UPDATE_ADDRESSES)]
    resources = list(planned_resources(document.get("planned_values", {}).get("root_module", {})))
    selected = lambda kind: [r["values"] for r in resources if r["type"] == kind]
    bucket = f"{PREFIX}-{EXPECTED_ACCOUNT}-media"
    functions = selected("aws_lambda_function")
    rules = selected("aws_cloudwatch_event_rule")
    mappings = selected("aws_lambda_event_source_mapping")
    apis = selected("aws_apigatewayv2_api")
    pools = selected("aws_cognito_user_pool")
    policies = selected("aws_s3_bucket_policy")
    notifications = selected("aws_s3_bucket_notification")
    identities = selected("aws_caller_identity")
    if not identities:
        # Terraform can omit already-read data sources from planned_values.
        # The plan's refreshed prior_state retains their identity result.
        prior = document.get("prior_state", {}).get("values", {}).get("root_module", {})
        identities = [r["values"] for r in planned_resources(prior) if r.get("type") == "aws_caller_identity"]
    checks = [
        check("course_account_confirmed", len(identities) == 1 and identities[0].get("account_id") == EXPECTED_ACCOUNT),
        check("no_resource_creation_deletion_or_replacement", not destructive),
        check("only_runtime_resource_types_updated", not unexpected),
        check("all_expected_functions_retained", {f"{PREFIX}-{x}" for x in ZIP_FUNCTIONS} <= {f.get("function_name") for f in functions}),
        check("all_project_functions_throttled", bool(functions) and all(f.get("function_name", "").startswith(PREFIX + "-") and f.get("reserved_concurrent_executions") == 0 for f in functions)),
        check("function_account_and_region_match", bool(functions) and all(f.get("arn", "").startswith(f"arn:aws:lambda:{EXPECTED_REGION}:{EXPECTED_ACCOUNT}:function:{PREFIX}-") for f in functions)),
        check("both_schedules_disabled", {r.get("name") for r in rules} == {f"{PREFIX}-outbox", f"{PREFIX}-cleanup"} and all(r.get("state") == "DISABLED" for r in rules)),
        check("queue_consumers_disabled", len(mappings) >= 3 and all(m.get("enabled") is False for m in mappings)),
        check("default_api_endpoint_disabled", len(apis) == 1 and apis[0].get("name") == f"{PREFIX}-api" and apis[0].get("disable_execute_api_endpoint") is True),
        check("self_signup_disabled", len(pools) == 1 and any(x.get("allow_admin_create_user_only") is True for x in pools[0].get("admin_create_user_config", []))),
        check("media_data_denied_management_preserved", len(policies) == 1 and policies[0].get("bucket") == bucket and paused_bucket_policy(policies[0].get("policy", "{}"), bucket)),
        check("s3_event_destinations_suspended", all(not n.get("queue") and not n.get("lambda_function") and not n.get("topic") and not n.get("eventbridge") for n in notifications)),
    ]
    return {"mode": "offline-plan-review", "checks": checks,
            "createOrDeleteAddresses": destructive, "unexpectedUpdateAddresses": unexpected,
            "updateCount": sum(r["change"]["actions"] == ["update"] for r in changes),
            "cloudVerified": False, "cognitoExistingUsersVerifiedDisabled": False,
            "boundary": "A passing plan is not an apply or runtime/billing verification. Existing users require separate reversible disable/restore handling."}


def inspect_cloud(profile):
    # SDK reads use the explicitly selected course profile, never personal MCP.
    import boto3
    from botocore.config import Config
    session = boto3.Session(profile_name=profile, region_name=EXPECTED_REGION)
    config = Config(retries={"mode": "standard", "total_max_attempts": 1}, connect_timeout=5, read_timeout=15)
    clients = {name: session.client(name, config=config) for name in
               ["sts", "events", "lambda", "apigatewayv2", "s3", "cognito-idp"]}
    identity = clients["sts"].get_caller_identity()
    if identity.get("Account") != EXPECTED_ACCOUNT:
        raise ValueError("ACCOUNT_MISMATCH: no project reads attempted")
    # A first denied course read stops the inventory, rather than retrying every resource.
    rules = [clients["events"].describe_rule(Name=f"{PREFIX}-{name}") for name in ["outbox", "cleanup"]]
    functions = [f for page in clients["lambda"].get_paginator("list_functions").paginate()
                 for f in page.get("Functions", []) if f["FunctionName"].startswith(PREFIX + "-")]
    function_rows = []
    mappings = []
    for function in functions:
        name = function["FunctionName"]
        concurrency = clients["lambda"].get_function_concurrency(FunctionName=name)
        function_rows.append({"name": name, "reservedConcurrency": concurrency.get("ReservedConcurrentExecutions")})
        for page in clients["lambda"].get_paginator("list_event_source_mappings").paginate(FunctionName=name):
            mappings.extend({"function": name, "state": m.get("State"), "uuid": m["UUID"]} for m in page.get("EventSourceMappings", []))
    apis = [a for page in clients["apigatewayv2"].get_paginator("get_apis").paginate()
            for a in page.get("Items", []) if a.get("Name") == f"{PREFIX}-api"]
    # Custom-domain mappings could bypass the default-endpoint switch. Read only this API's mappings.
    custom_mapping_count = 0
    api_ids = {a["ApiId"] for a in apis}
    for page in clients["apigatewayv2"].get_paginator("get_domain_names").paginate():
        for domain in page.get("Items", []):
            for mapped in api_mapping_pages(clients["apigatewayv2"], domain["DomainName"]):
                custom_mapping_count += sum(m.get("ApiId") in api_ids for m in mapped.get("Items", []))
    bucket = f"{PREFIX}-{EXPECTED_ACCOUNT}-media"
    policy = clients["s3"].get_bucket_policy(Bucket=bucket)["Policy"]
    notification = clients["s3"].get_bucket_notification_configuration(Bucket=bucket)
    pools = [p for page in clients["cognito-idp"].get_paginator("list_user_pools").paginate(MaxResults=60)
             for p in page.get("UserPools", []) if p.get("Name") == f"{PREFIX}-users"]
    pool_rows = []
    for pool in pools:
        config_pool = clients["cognito-idp"].describe_user_pool(UserPoolId=pool["Id"])["UserPool"]
        users = [u for page in clients["cognito-idp"].get_paginator("list_users").paginate(UserPoolId=pool["Id"]) for u in page.get("Users", [])]
        pool_rows.append({"id": pool["Id"], "selfSignupDisabled": config_pool.get("AdminCreateUserConfig", {}).get("AllowAdminCreateUserOnly") is True,
                          "userCount": len(users), "enabledUserCount": sum(u.get("Enabled", False) for u in users)})
    checks = [
        check("both_schedules_disabled", all(r.get("State") == "DISABLED" for r in rules)),
        check("all_expected_functions_retained", {f"{PREFIX}-{x}" for x in ZIP_FUNCTIONS} <= {f["name"] for f in function_rows}),
        check("all_project_functions_throttled", bool(function_rows) and all(f["reservedConcurrency"] == 0 for f in function_rows)),
        check("queue_consumers_disabled", len(mappings) >= 3 and all(m["state"] == "Disabled" for m in mappings)),
        check("default_api_endpoint_disabled", len(apis) == 1 and apis[0].get("DisableExecuteApiEndpoint") is True),
        check("no_custom_domain_bypass", custom_mapping_count == 0),
        check("media_data_denied_management_preserved", paused_bucket_policy(policy, bucket)),
        check("s3_event_destinations_suspended", not any(notification.get(k) for k in ["QueueConfigurations", "TopicConfigurations", "LambdaFunctionConfigurations"]) and "EventBridgeConfiguration" not in notification),
        check("self_signup_disabled", len(pool_rows) == 1 and pool_rows[0]["selfSignupDisabled"]),
        check("existing_user_signin_disabled", len(pool_rows) == 1 and pool_rows[0]["enabledUserCount"] == 0),
    ]
    return {"mode": "live-read-only", "account": EXPECTED_ACCOUNT, "region": EXPECTED_REGION,
            "checks": checks, "functions": function_rows, "mappings": mappings, "pools": pool_rows,
            "rules": [{"name": r["Name"], "state": r["State"]} for r in rules],
            "customApiMappingCount": custom_mapping_count,
            "boundary": "Configuration reads do not prove zero charges, stopped in-flight work, or old signed-URL rejection. No credentials, user identities, environment values or object data are emitted."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-json", type=Path, help="Offline terraform show -json file; contains sensitive state, keep under ignored tmp")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--output", type=Path, required=True, help="Sanitized report path, normally ignored tmp")
    args = parser.parse_args()
    report = {"schemaVersion": 1, "checkedAtUtc": datetime.now(timezone.utc).isoformat(),
              "readOnly": True, "mutations": False, "zeroCostGuaranteed": False,
              "expectedAccount": EXPECTED_ACCOUNT, "region": EXPECTED_REGION}
    try:
        if args.plan_json:
            report.update(inspect_plan(json.loads(args.plan_json.read_text(encoding="utf-8-sig"))))
        else:
            if args.profile != "default":
                raise ValueError("COURSE_PROFILE_REQUIRED: use default; personal profile is out of scope")
            report.update(inspect_cloud(args.profile))
        report["passed"] = all(item["passed"] for item in report["checks"])
        exit_code = 0 if report["passed"] else 1
    except Exception as error:
        # Top-level failure report deliberately excludes messages containing principal ARNs.
        response = getattr(error, "response", {})
        message = response.get("Error", {}).get("Message", "")
        report.update(passed=False, status="blocked", errorType=type(error).__name__,
                      errorCode=response.get("Error", {}).get("Code"),
                      operation=getattr(error, "operation_name", None),
                      studentLabCancelPolicy="voc-cancel-cred" in message.lower())
        exit_code = 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "exitCode": exit_code, "report": str(args.output)}))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
