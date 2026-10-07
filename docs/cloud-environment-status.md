# Cloud configuration snapshot — 2026-10-07, amended 2026-10-08

## Latest continuation, 8 October

Read the [continuation acceptance report](acceptance-continuation-20261008.md)
for the current source and quality results. The previous report and snapshot
below remain historical evidence and are not overwritten.

The final merged checks pass **184 unique automated tests**: 88 backend,
29 frontend, 59 inference/auth, and 8 unchanged Oracle validator checks retained
from the previous run. This continuation executed 176 tests, plus frontend build
and AWS/GCP Terraform validation. A real local-model integration run passes
52 functional checks using memory storage/database, local principals and an empty
Rekognition fixture. It does not validate the live dual-cloud ingestion pipeline.

An additional eight independent Oxford images provide a semantic-only baseline,
not individual pet identities. DINO-only classified all four cats as fox and
produced no pet features; the four dogs were classified as dog. Missing gallery
features make all 14 query cases fail conservatively (`feature_failures`, exit 1).
The earlier correct-text miss remains. A query using the same pet photo with
irrelevant car text still returns a candidate; this raises a scoring question,
but does not establish an individual-identity false positive. Thresholds and
scoring were not changed to make these samples pass.

Reviewed Terraform apply completed with 0 additions, 7 changes, 0 deletions.
Only `source_code_hash`/`last_modified` changed on the seven existing Lambdas;
roles, permissions and environment settings stayed unchanged. All seven functions
are Active/Successful and match v2, LabRole and ZIP SHA-256
`fa8448c2a0fdff09637ed01fc575c22f0aa6861634d042c283f7968b86f7fdd2`.
The new two-user cloud run passes 29/29, sends no emails, and reports no cleanup
errors. A separate real cleanup probe passes 29/29: repeated late source writes
are reclaimed; FAILED retry inputs and active leases are protected; its three
fixture records, three keys and all versions were removed. Bucket versioning
remained Disabled. The production late-event ingest path is still absent and
was tested only locally. Cleanup counts eligible operations, not files or bytes.

Uploads still reach QUEUED, pet queries return INFERENCE_UNAVAILABLE, and chat
returns LEX_UNAVAILABLE. Those are correct failure boundaries, not completed
business flows. OCI reinspection still finds no RUNNING/STOPPED host. Personal
AWS MCP identity works and Sydney/us-east-1 ListBots are empty; no formal app bot
is configured. Google remains the fixed WIF service identity, not a compute host.
Expired Cognito JWKS-cache handling is fixed and tested locally, but Oracle has
not received that deployment because there is no confirmed runtime host.

**Current Lab shutdown: confirmed in Edge as Terminated / 00:00.**
Fresh course-account EC2 inventory immediately before shutdown found zero
running, pending, stopping or stopped instances in us-east-1 and us-west-2.
The current two-user test and cleanup probe removed their owned test data; both
local model workers exited and their temporary files were removed. No frontend
was started in this continuation. The temporary extension file-URL permission
still requires the user's confirmation that it has been turned off.
Current evidence is `lab-ended-continued.json` and `lab-ended-continued-final.png`
in the ignored acceptance directory. The screenshot shows the red AWS status
indicator and 00:00; accessibility state separately confirmed Terminated.
The A$0 additional-spend limit remains in effect. Browser upload URL lifetime
(plan 600s/code 900s) and query deadline (plan 15min/code 3600s) remain explicit
unresolved plan deviations. Success and permanent failure immediately attempt
query cleanup; cancellation enters asynchronous cleanup immediately, with active
producer leases protected until a worker or periodic sweep can reclaim the input.

## Previous full-test snapshot, 8 October

The user authorized complete testing after the setup-only pause. The course Lab
was resumed only for this verification. Reviewed incremental Terraform changes
deployed OPTIONS, the frontend Idempotency-Key CORS header, and the seven current
v2 Lambda packages. Real Cognito/two-user API boundaries, signed S3 bytes/checksum,
deletion Lambda and Rekognition passed. All seven final code hashes match the ZIP.
The final core-fix apply changed only code hashes/modified timestamps on these
seven existing functions. Their Active/Successful state, v2 manifest and unchanged
LabRole were verified against ZIP SHA-256
`c21e1e6e1ad1a680b40ecc86919d1600d037ded791a4798ee7e0abba8ef26d0b`.
The merged source passes 137 unique automated tests and the frontend build;
deletion races, partial-batch retries and pet-result producer/consumer fields
were repaired. This does not close the retrieval-quality or deployment blockers.
After that apply, all 29 two-user checks passed again. The existing cleanup
Lambda also passed 19 focused recovery checks with isolated, expired ARTIFACTS
records and a real S3 object; ledger/object deletion and producer fencing were
verified, and all test data was removed. No READY ingestion result was fabricated.
The course Lab was then ended in Edge and verified as AWS Status: Terminated,
Lab terminated and 00:00. The owned frontend process stopped (no port 5173
listener), and the signed-out test app tab closed. Temporary test users, objects,
records, SNS topic/subscription and password file were removed. The user still
needs to confirm restoration of the temporary extension file URL permission.

A disposable real LabRole Lambda successfully exchanged Google WIF credentials,
minted and verified the intended service ID token, and rejected a wrong audience.
The actual voclabs role was rejected by the provider's attribute condition. The
probe, ZIP and its log group were removed. This establishes the identity chain,
but no actual Oracle HTTPS receiving service exists yet.

OCI IAM confirms the configured principal is the student's jlii0474 account. All
configured profiles point to that same tenancy; Melbourne is its sole subscribed
region. Cross-checked inventory has no RUNNING/STOPPED VM, one TERMINATED OKE node,
and a zero-size node pool. Historical SSH timed out; the old LB has no backend and
no HTTPS endpoint. No Oracle resource was created or started.

The live upload still stops at QUEUED because the ECR worker image, ingest Lambda,
and inference endpoint are absent; application pet query and Lex return accurate
503 errors. Correct text retrieval still fails the current similarity threshold.
The user authorized the QQ test mailbox; actual Cognito and SNS confirmation
succeeded. The real notify Lambda published one authorized notification and the
user confirmed receipt; replay, private-other-owner, nonmatching tags and API
cancellation prevented new deliveries. This uses an explicit notification fixture,
not completed ingestion. The browser file chooser and actual S3 bytes also passed.

See [previous test results](environment-acceptance-20261008.md) and
[rubric status](acceptance-matrix.md). The historical snapshot below is retained
as prior evidence and does not override this update.

## Historical snapshot

The historical deployment details below are retained. On 8 October the student
Lab was verified ended (Terminated / 00:00). OCI DEFAULT API configuration was
repaired and verified; the accessible Oracle configuration had no running VM and
the OKE node pool had size 0. No server was started. The four-person implementation
plan assigns environment/deployment work to C and Lex/notification work to D.

Personal AWS profile ecoguard-personal belongs to account 011154879765, defaults
to ap-southeast-2, and is separate from course resources. Lex ListBots succeeded
in Sydney and us-east-1. Free Tier is FREE/ACTIVE, with US$100 remaining credit
and plan expiry on 7 April 2027 at the time of the check. A temporary Sydney bot
was created, built, published and successfully invoked once, then its bot/alias
and role were deleted and verified absent. Results and limits are recorded in
lex-resolution.md. This does not migrate
the course deployment or complete application acceptance.

User instruction: configure cloud environments, then stop; user will try Lex manually.
Latest provider selection: AWS course allocation + existing Oracle server. Extra
personal spend limit A$0.

## AWS deployed

- Account 360493183467; region us-east-1; execution role LabRole.
- Cognito pool us-east-1_9nR2hwyVJ; client 6io5inllom6a5l43038uhbluvi.
- API https://i4zicvj5s2.execute-api.us-east-1.amazonaws.com.
- Private bucket ecoguard3184-dev-360493183467-media; AES256, public blocking,
  owned-bucket event policy, explicit CORS origins and HTTPS-only bucket policy.
- Media/operations/search/subscriptions/outbox DynamoDB tables, four SQS + DLQs.
- Seven zip Lambdas and API JWT authorizer; outbox/cleanup schedules.
- Processing disabled pending a real FFmpeg worker image and protected inference
  endpoint. ECR repository exists, image not built/pushed.

Actual Cognito check: forced temporary password change passed; access token
accepted; anonymous and ID token rejected; disposable test user removed. Email
verification was not tested and no email was sent. Preflight returned 401 because
the ANY business route was protected; explicit unauthenticated OPTIONS route and
204 handler have been prepared. The fix, current package and model v2 environment
update await fresh course credentials.

At about 23:24 Sydney the Learner Lab applied voc-cancel-cred and management calls
began failing. This is session expiry, separate from the persistent Lex denial.
The Lab is now ended. Only resume it when separately authorized and needed for
deployment; then refresh the local AWS credentials before continuing. Do not
put temporary credentials into this project, Terraform variables or screenshots.

## Free workload identity configured

- Google project ecoguard-fit3184-jlii0474 / number 1086936100292.
- Billing disabled; no billed compute/storage/registry resources created.
- IAM, IAM Credentials and STS APIs; ecoguard-aws WIF pool / aws-role provider.
- Only account 360493183467 + assumed-role/LabRole is admitted.
- Invoker ecoguard-aws-invoker@ecoguard-fit3184-jlii0474.iam.gserviceaccount.com.
- config/google-wif.json contains only public external-account settings; no key.
- Real Lambda-to-Google token exchange awaits valid AWS lab session and an Oracle
  endpoint. IAM use is free per [official pricing](https://cloud.google.com/iam/pricing).

## Oracle pending access

The locally configured SMARTPARK tenancy in ap-melbourne-1 returned only a terminated
OKE node; no independent running server was found in that profile. DEFAULT OCI
API-key access was repaired on 8 October; this does not create a runtime server.
No Oracle resource has been created, restarted, resized or
changed. Waiting for the actual intended server's address, SSH username and key
path (or clarification that the OKE cluster is intended).

Inference service and Oracle templates are prepared. The service independently
verifies full Google ID token signatures and Cognito access tokens. HTTPS proxy
and compute capacity must be checked on the intended server before it is exposed.
Existing services must be inspected and preserved. Do not silently activate the
paid SmartPark node pool or create another VM to stand in for the user's server.

## Lex manual handoff

See manual-lex-setup.md and services/aws/tools/deploy_lex.py. The current course
caller and LabRole do not have verified Lex rights. Console operations cannot
grant rights absent from the account. Deploy only with a role already trusted by
lexv2.amazonaws.com and a permitted caller; then connect bot/alias IDs to the API.

The project is not yet fully cloud configured or assignment accepted. Required
remaining configuration: fresh Lab session + CORS/package update; access to the
actual Oracle server + image/HTTPS/inference setup; real WIF validation and worker
activation. Lex is deliberately left for the user's manual attempt.
