# Deployment and verification

This runbook follows the four-person implementation plan: C owns infrastructure,
identity and deployment, A owns core/model logic, B owns UI and client auth, and D
owns Lex/notifications. The user's extra personal spending limit is A$0. Use only
course allocation or verified existing credits/free coverage. Personal AWS Free
Tier was verified FREE/ACTIVE with US$100 credit on 8 October; never upgrade its
plan automatically. The second compute cloud is an existing Oracle server, still
to be located and verified. GCP is limited to the existing free IAM/WIF setup.
Planning and local Toolkit access do not authorize starting the student Lab or
Oracle resources. Check actual account, region, scope and budget before deployment.

## Local

```powershell
.\scripts\bootstrap-local.ps1
.\scripts\dev.ps1 api
# In another terminal, explicitly set apps/web/.env.local VITE_DEV_MODE=true
.\scripts\dev.ps1 web
.\scripts\verify.ps1
```

Development authentication is visibly labelled and works only on loopback.
Missing real inference, Cognito, SNS and Lex never become simulated success.

## AWS foundation

The student Learner Lab is ended. If a separately authorized deployment needs it,
resume the Lab and refresh temporary credentials in the standard local credentials
file. Never paste them into the repository. Reuse the existing foundation and
review an incremental plan; do not recreate its bucket or tables by default.
The commands below are deployment steps, not work to execute merely to read the
four-person plan. Check whether bucket bootstrap is actually needed first.

```powershell
.\scripts\bootstrap-aws-bucket.ps1
Set-Location infra/aws
terraform init
terraform plan -out=foundation.tfplan
terraform apply foundation.tfplan
```

The bucket is retained outside Terraform's bucket resource because this course's
SCP denies the provider's Object Lock refresh. See the S3 decision document.
Terraform still manages its privacy, encryption, HTTPS, CORS and event settings.

## Protected API

```powershell
# From repository root
.\scripts\build-aws-zip.ps1
Set-Location infra/aws
terraform plan -var=deploy_api=true -out=api.tfplan
terraform apply api.tfplan
```

Use the output Cognito IDs and API URL in apps/web/.env.local; set
VITE_DEV_MODE=false. Keep all business routes behind the JWT authorizer. The API
additionally requires token_use=access and the exact client_id. An unauthenticated
request must return 401. Test real registration/email verification and temporary
password changes using accounts and mailboxes controlled by the team.

## Cross-cloud inference

Use the existing GCP IAM/WIF identities and public config/google-wif.json.
AWS federation must allow only the intended account and execution role. Do not
create a Cloud Run service, GCS model bucket or Artifact Registry for this route.
If moving any workload to a different AWS account, update and verify the allowed
identity deliberately; personal CLI login is not an automatic migration.

Follow infra/oracle/README.md for the existing-host preflight, locked model cache,
container configuration, loopback binding and existing HTTPS proxy. At present no
running target host has been confirmed; keep cloud deployment blocked until it
exists within the budget. Do not start paid OKE nodes or create a replacement VM.
The current template supports linux/amd64; an ARM host requires compatible locks,
image and real model verification first. Preserve existing websites and services.

Oracle application code must fully verify Google service ID-token signatures,
issuer, audience, expiry and the permitted identity, independently from Cognito
user access-token verification. `/v1/query-authorizations` verifies the forwarded
user and must meet its bounded request budget without loading model weights.
Validate allowed and rejected identities over real HTTPS. Fix all model/manifest
hashes and record cold/warm latency, memory and maximum-video behavior before
routing uploads; local success is not a dual-cloud acceptance result.

Build `services/aws/Dockerfile` for linux/amd64 and push to the output ECR
repository, recording its digest. If local Docker remains unavailable, use a
permitted managed build after credits are confirmed. Never alter BIOS/boot or
reboot automatically to unblock this build.

Pass worker_image, inference_url and google_invoker_email to infra/aws. Processing
and S3 notifications remain disabled until all three are present. Reapply the API
zip after generating WIF configuration. Confirm queues carry task references,
not JWTs or presigned URLs. Run two-user privacy and deletion-race checks against
the actual deployment, not fixtures.

## Lex and release

Lex remains a required dependency. The personal account passed a real temporary
create/build/publish/text-runtime probe, and the probe resources were cleaned up;
see lex-resolution.md. The course account denial remains unchanged. C/D must
verify the intended account, runtime caller, region and fulfillment path before
formal deployment. Create a bot role trusted by Lex, deploy intents and restricted Lambda
code-hook permission, then configure lex_bot_id/lex_alias_id and verify a real
conversation. A local fallback does not establish Lex acceptance.

Host the built frontend over HTTPS and put its exact origin in frontend_origins
and web_url. Record live endpoint, immutable image/manifest versions, model
benchmarks, complete demo evidence and actual team contributions before the
report is finalized. Keep an authorized, budget-compatible runtime plan for all Week 15 assessments.
Do not run terraform destroy during marking.
