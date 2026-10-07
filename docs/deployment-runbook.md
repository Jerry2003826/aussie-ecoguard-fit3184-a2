# Deployment and verification

Only AWS course allocation and confirmed existing Google credits may be used.
The user's extra personal spending limit is A$0. An enabled billing account does
not establish available credit. Do not deploy GCP until credit amount, expiry and
service eligibility have been confirmed.

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

Refresh the Learner Lab temporary credentials in the standard AWS credentials
file. Never paste them into the repository. Run the bucket bootstrap first.

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

After credit confirmation, select a dedicated GCP project; do not repurpose a
different coursework project without checking its existing resources. Apply
infra/gcp first without an image to create service identities, WIF, the model
bucket and Artifact Registry. AWS federation must permit only the exact account
and execution role. No service-account private key is used.

Create config/google-wif.json using `gcloud iam workload-identity-pools
create-cred-config` with --aws and the output invoker account. This is a public
identity configuration, not a credential secret; nevertheless review it before
committing. Rebuild the AWS zip and worker image to include it. The containers
must never contain AWS keys or a GCP private key.

Build `services/inference/Dockerfile` with the repository as context. Use the
actual locked manifest and its SHA-256; update the hash if any byte changes.
Publish immutable image digests, set GCP image and Cognito/S3 configuration, and
apply the protected Cloud Run service. Keep max instances 1, min 0, concurrency
1, timeout 360 seconds and model deadline 240 seconds initially. Confirm both
unsigned and wrong-identity requests fail. `/v1/query-authorizations` also
validates the forwarded Cognito token and must complete within 8 seconds without
loading model weights. Warm up explicitly and measure CPU latency before routing
uploads. Real benchmark data is required for species/count/pet accuracy claims.

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

Lex remains a required dependency. See lex-permission-request.md for the confirmed
Learner Lab denial and prepared teaching-team question. Once permission is
available, create a bot role trusted by Lex, deploy intents and restricted Lambda
code-hook permission, then configure lex_bot_id/lex_alias_id and verify a real
conversation. A local fallback does not establish Lex acceptance.

Host the built frontend over HTTPS and put its exact origin in frontend_origins
and web_url. Record live endpoint, immutable image/manifest versions, model
benchmarks, complete demo evidence and actual team contributions before the
report is finalized. Preserve the deployment through all Week 15 assessments.
Do not run terraform destroy during marking.
