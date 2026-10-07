# Cloud configuration snapshot — 2026-10-07

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
Restart Lab and update the local AWS credentials file before continuing. Do not
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
session is expired. No Oracle resource has been created, restarted, resized or
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
