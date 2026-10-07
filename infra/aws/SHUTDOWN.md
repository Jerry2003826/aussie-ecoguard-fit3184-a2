# Pause this project's runtime while retaining data

This switch has now been applied to the existing course stack and verified live.
The first plan made 14 in-place pause updates, with no creates/deletes; the final
code plan only updated the seven retained Lambda ZIPs and v3 manifest environment.
All seven keep reserved concurrency zero. The live verifier and old signed-URL
probes passed; the API returned 404 with DisableExecuteApiEndpoint=true and no
custom-domain bypass. Current Cognito users: zero, so no user accounts were altered.
See [the final core and pause record](../../docs/core-completion-20261008.md).

`deployment.auto.tfvars` now persists runtime_enabled=false; the overlay below is
also available for other reviewed deployments. Recovery must preserve all existing
resource existence inputs and requires fresh authorization. Historical preflight
limitations below remain as evidence, not a claim that management access is still denied.
The user chose to retain cloud data and configuration and accepts possible storage
allowance consumption. Extra out-of-pocket budget remains A$0. Do not destroy the
stack, upgrade a billing plan, start an unrelated server, or change SmartPark.

## Scope and current access

- Course account `360493183467`, profile `default`, region `us-east-1`, prefix
  `ecoguard3184-dev` only. The existing bucket is retained outside Terraform's
  bucket resource; the module manages its access policy and related settings.
- On 8 October the ended Lab still allowed STS identity lookup but denied the
  first `events:DescribeRule` with an explicit `voc-cancel-cred` denial. End Lab
  alone does not establish that schedules, queue pollers or the API are disabled.
  A fresh, explicitly authorized course session is needed to inspect/apply this
  plan; do not attempt to bypass the course restriction.
- Personal MCP is account `011154879765`, not the course account. Its latest
  read-only Free Tier response was FREE/ACTIVE with US$100 remaining; Lex bot
  lists were empty in Sydney and us-east-1. These are not a complete bill.
- GCP `ecoguard-fit3184-jlii0474` has the previously verified IAM/WIF-only setup,
  billing disabled and no project compute/storage deployment. Keep
  `identity_only=true`; do not disable APIs or resources belonging to other projects.
- The previous authoritative OCI inventory found no available VM for EcoGuard.
  No Oracle application was deployed. Do not modify the unrelated SmartPark OKE,
  load balancer, node pool or tenancy resources to close this project.

## What the switch changes

Set `runtime_enabled=false` while preserving **all existing deployment inputs**.
In particular, preserve `deploy_api=true`, the existing worker image if any, and
the final reviewed ZIP. Setting `deploy_api=false` or clearing `worker_image`
changes resource existence and is not a shutdown procedure.

| Existing resource | Paused configuration | Restore |
| --- | --- | --- |
| outbox/cleanup EventBridge rules | `state=DISABLED`; retain targets/permissions | Same rules `ENABLED` |
| Every project SQS event-source mapping | `enabled=false`; retain queue and mapping | Previous processing-ready gates apply |
| Every project Lambda, including ingest if it exists | Reserved concurrency `0` | Remove reservation (`-1` in this module), matching the pre-pause configuration |
| HTTP API | Disable default `execute-api` endpoint | Enable default endpoint |
| Existing S3 event notification, if deployed | Empty its destinations without changing the resource count | Restore the same owned-bucket destination |
| Dedicated media bucket | Add `DenyDataAccessWhileRuntimePaused` | Remove only this additional statement |
| Cognito user pool | Disable public self-sign-up | Restore public self-sign-up |

The bucket deny applies to all principals for object reads/writes/deletes,
restoration, multipart operations and object listing, on this bucket only. It also
rejects requests made with **previously issued presigned GET/PUT URLs** after the
new policy is effective. It does not deny `GetBucketPolicy`/`PutBucketPolicy`, so a
permitted administrator can restore the Terraform setting. The existing TLS-only
rule remains. Do not substitute a blanket `s3:*` deny, which can impede recovery.
An in-progress download or invocation may finish; the switch is not retroactive.

API default-endpoint disabling does not disable a custom-domain mapping. None is
defined here, but inspect live mappings before declaring the application closed.
Do not create a WAF, new proxy or billing resource to close an endpoint.

## Cognito and administrator access

Closing the application API and self-sign-up does **not** prevent existing users
from calling Cognito sign-in directly. If complete end-user sign-in suspension is
required, the operator must separately list this pool's currently enabled users,
save a private restore list outside Git, and use `AdminDisableUser` only for those
users. Preserve every user and client. On resume use `AdminEnableUser` only for
users disabled by this pause, not users already disabled beforehand. Do not print
usernames/emails or store the restore list in Terraform or the repository.

The read-only verifier counts enabled users, without emitting identities, and
does not report full pause success while any remain enabled. It never disables or
enables users itself. Do not remove the Cognito client, change authentication flows
to an assumed unsupported empty value, or add a paid access-control service.

Cloud administrators retain recovery/control-plane access. Direct administrator
operations against retained DynamoDB/SNS/etc. are outside the end-user runtime
shutdown; do not alter the shared LabRole or account-wide policies.

## Review, execution order and verification

Finish the authorized core verification before the full pause. Earlier stopping
of the two maintenance schedules can reduce periodic work, but must be coordinated
with tests and cleanup. Do not disable cleanup while relying on it to remove test
input. Resolve/record pending work, finish in-flight producers, and clear only
owned temporary test data before pausing. Retained SQS messages still expire after
their existing retention period (work queues 1 day, DLQs 14 days); existing table
TTL and log retention continue. This switch is not an indefinite job/log archive.
With cleanup stopped, remaining temporary S3 input can outlive its application
deadline and consume storage until a deliberate cleanup or resume.

1. Verify fresh `default` identity equals the course account and the operation
   region is us-east-1. Save the current project settings and enabled-user restore
   list privately. Check for provisioned concurrency, custom-domain mappings or
   out-of-Terraform triggers; none is configured by this module. Unexpected
   resources require review, not account-wide disabling.
2. With the **same current input files and final Lambda ZIP**, create a Terraform
   plan adding `-var-file=runtime-disabled.tfvars.example`. The plan must have
   **0 creates, 0 deletes, no replacements**, and only reviewed in-place changes.
   A final reviewed ZIP update may appear alongside concurrency changes. Reject
   any unrelated code/environment/role/storage change. Never apply a stale plan.
3. Export `terraform show -json` to an ignored private path and run the offline
   checker below. Terraform plan/state JSON can contain sensitive values; only
   the checker report is sanitized. A passing plan is not a cloud shutdown.
4. The operator applies the reviewed plan. Prefer stopping event producers and
   queue pollers before throttling functions to avoid retry churn; a Terraform
   apply is not atomic. The API and bucket gates prevent new user work. Wait for
   configuration propagation and previously running work to finish. If a course
   SCP rejects `PutFunctionConcurrency` or another change, report partial state:
   disabled rules/mappings plus blocked API/S3 may reduce execution, but are not
   proof that every invoke path is blocked. Never claim success from the plan.
5. Separately complete/review Cognito user suspension if the requirement is all
   user access. Run the live read-only verifier. Every required check must pass;
   permissions failures mean **unverified**, not disabled.
6. For end-to-end S3 proof, use an operator-owned tiny fixture and presigned
   GET/PUT issued *before* the pause, stored only in a private file. After pause,
   each must receive `403 AccessDenied` while the signature/credentials would
   otherwise still be valid; an expired token alone does not prove this policy.
   The write probe may only target that fixture's new key, never existing user
   media. This is a separately reviewed probe, not part of the read-only checker.
   Save HTTP status/error category only, never the signed URL or response body.
   Confirm bucket-policy read/manage access remains available. Retain fixture
   cleanup instructions privately if the data deny now blocks deletion.
7. Verify the default API rejects access and there is no custom-domain bypass.
   Record schedules/mappings/concurrency and any queued retry limitations. End
   the student Lab and verify its Terminated/00:00 state after all checks.

Example checker calls from the repository root (the operator creates the private
plan file; these commands themselves do not mutate AWS):

```powershell
.venv\Scripts\python.exe scripts/check-project-shutdown.py `
  --plan-json tmp/project-shutdown-20261008/disabled-plan.json `
  --output tmp/project-shutdown-20261008/plan-review.json

.venv\Scripts\python.exe scripts/check-project-shutdown.py `
  --profile default `
  --output tmp/project-shutdown-20261008/live-verification.json
```

Exit 0 means that mode's explicit checks passed, 1 means unmet checks, and 2 means
blocked/invalid input. No mode sends emails, invokes models/functions, reads user
media, starts a Lab, changes resources, or guarantees billing outcome.

## Costs that can remain

This is **runtime suspension with retained data**, not a promise of zero charges.
S3 object/version/multipart storage, DynamoDB table storage and existing backups,
CloudWatch logs and ECR images can continue using storage allowances or credits.
The current module uses on-demand DynamoDB and does not define provisioned Lambda
concurrency, NAT gateways, EC2, load balancers or paid GCP compute. Verify drift.
Existing charges and requests already processed are not reversed by shutdown.
Rejected external requests, authentication and retained subscriptions require
separate billing interpretation; do not assume disabling an API guarantees zero
request charges. Malicious/public requests cannot be fully cost-bounded by this
application switch alone.

Cost Explorer API requests themselves cost US$0.01 per paginated request. No such
query was made during this investigation under the A$0 constraint. Free Tier usage
was empty, which is neither a service-by-service bill nor proof of zero spend.
Use an authorized billing-console view or a separately approved, funded cost
query if a full billing breakdown is required; do not enable new reports,
Compute Optimizer, budgets/alarms, Athena scans or billing plans automatically.

## Sources

- [Lambda reserved concurrency and setting zero](https://docs.aws.amazon.com/lambda/latest/dg/configuration-concurrency.html)
- [Disable an HTTP API default endpoint](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-disable-default-endpoint.html)
- [Presigned requests obey current permission policies](https://docs.aws.amazon.com/prescriptive-guidance/latest/presigned-url-best-practices/overview.html)
- [S3 policies restricting presigned requests](https://docs.aws.amazon.com/AmazonS3/latest/userguide/bucket-policy-s3-sigv4-conditions.html)
- [Disable and re-enable Cognito users](https://docs.aws.amazon.com/cognito/latest/developerguide/how-to-manage-user-accounts.html)
- [Cognito self-sign-up policy](https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-settings-admin-create-user-policy.html)
- [Cost Explorer API pricing](https://aws.amazon.com/aws-cost-management/aws-cost-explorer/pricing/)
