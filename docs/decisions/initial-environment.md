# Initial environment and blockers

Verified 2026-10-07 (Sydney). Extra personal spend limit is A$0.

- Windows, Node 24.14.1, Python 3.12.13 installed with uv, Git 2.53, Terraform 1.16.5 verified against vendor SHA256.
- AWS Learner Lab identity is valid. Cognito/S3/DynamoDB/SQS/Rekognition list checks succeed.
- Lex ListBots and actual CreateBot return AccessDeniedException. No bot was created. LabRole does not trust lexv2.amazonaws.com.
- User confirms this is the only AWS account; teaching-team clarification is required. Do not replace Lex with a simulated chat implementation in acceptance.
- Google CLI logged in and billing account open; actual credits, expiry and eligible services remain unconfirmed. Billable GCP deployment waits for those facts.
- Docker Desktop cannot start its Linux engine; WSL reports virtualization prerequisites unavailable. Do not change BIOS, boot settings or reboot as part of this task. Use a permitted remote build when credits are established, or a Linux-compatible zip for applicable Lambda handlers.
- Git uses local repository identity Jiarui Li with the GitHub noreply address. Branch Jiarui/main.

These blockers affect cloud acceptance. Local implementation, contracts, deterministic rules and UI continue.
