locals {
  zip_functions = var.deploy_api ? {
    api    = 29, infer = 480, delete = 120, notify = 60,
    outbox = 60, cleanup = 120, lex_fulfillment = 20
  } : {}
  has_worker       = var.deploy_api && var.worker_image != ""
  processing_ready = local.has_worker && var.inference_url != "" && var.google_invoker_email != ""
  role_arn         = var.execution_role_arn != "" ? var.execution_role_arn : "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/LabRole"
  runtime_environment = {
    ECO_MODE                    = "aws"
    ECO_REGION                  = var.region
    ECO_MEDIA_TABLE             = aws_dynamodb_table.data["media"].name
    ECO_OPERATIONS_TABLE        = aws_dynamodb_table.data["operations"].name
    ECO_INDEX_TABLE             = aws_dynamodb_table.data["search"].name
    ECO_SUBSCRIPTIONS_TABLE     = aws_dynamodb_table.data["subscriptions"].name
    ECO_OUTBOX_TABLE            = aws_dynamodb_table.data["outbox"].name
    ECO_MEDIA_BUCKET            = local.bucket_name
    ECO_INGEST_QUEUE_URL        = aws_sqs_queue.work["ingest"].url
    ECO_INFERENCE_QUEUE_URL     = aws_sqs_queue.work["infer"].url
    ECO_DELETE_QUEUE_URL        = aws_sqs_queue.work["delete"].url
    ECO_NOTIFICATION_QUEUE_URL  = aws_sqs_queue.work["notify"].url
    ECO_COGNITO_CLIENT_ID       = aws_cognito_user_pool_client.web.id
    ECO_COGNITO_USER_POOL_ID    = aws_cognito_user_pool.users.id
    ECO_INFERENCE_URL           = var.inference_url
    ECO_GOOGLE_INVOKER_EMAIL    = var.google_invoker_email
    ECO_GOOGLE_CREDENTIALS_FILE = "/var/task/config/google-wif.json"
    ECO_WEB_URL                 = var.web_url
    ECO_API_URL                 = var.deploy_api ? aws_apigatewayv2_api.app[0].api_endpoint : ""
    ECO_MODEL_MANIFEST_VERSION  = var.model_manifest_version
    ECO_LEX_BOT_ID              = var.lex_bot_id
    ECO_LEX_ALIAS_ID            = var.lex_alias_id
    ECO_RESOURCE_PREFIX         = local.name
  }
}
resource "aws_cloudwatch_log_group" "functions" {
  for_each          = merge(local.zip_functions, local.has_worker ? { ingest = 120 } : {})
  name              = "/aws/lambda/${local.name}-${each.key}"
  retention_in_days = 7
}
resource "aws_lambda_function" "zip" {
  for_each         = local.zip_functions
  function_name    = "${local.name}-${each.key}"
  role             = local.role_arn
  filename         = var.lambda_zip
  source_code_hash = var.deploy_api ? filebase64sha256(var.lambda_zip) : null
  runtime          = "python3.12"
  architectures    = ["x86_64"]
  handler          = "ecoguard_aws.handlers.${each.key}"
  timeout          = each.value
  memory_size      = 512
  environment { variables = local.runtime_environment }
  depends_on = [aws_cloudwatch_log_group.functions]
}
resource "aws_lambda_function" "ingest" {
  count         = local.has_worker ? 1 : 0
  function_name = "${local.name}-ingest"
  role          = local.role_arn
  package_type  = "Image"
  image_uri     = var.worker_image
  architectures = ["x86_64"]
  image_config { command = ["ecoguard_aws.handlers.ingest"] }
  timeout     = 120
  memory_size = 2048
  ephemeral_storage { size = 1024 }
  environment { variables = local.runtime_environment }
  depends_on = [aws_cloudwatch_log_group.functions]
}
resource "aws_apigatewayv2_api" "app" {
  count         = var.deploy_api ? 1 : 0
  name          = "${local.name}-api"
  protocol_type = "HTTP"
  cors_configuration {
    allow_origins = var.frontend_origins
    allow_methods = ["GET", "POST", "PATCH", "DELETE", "OPTIONS"]
    allow_headers = ["authorization", "content-type"]
    max_age       = 300
  }
}
resource "aws_apigatewayv2_authorizer" "cognito" {
  count            = var.deploy_api ? 1 : 0
  api_id           = aws_apigatewayv2_api.app[0].id
  name             = "CognitoAccessToken"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  jwt_configuration {
    audience = [aws_cognito_user_pool_client.web.id]
    issuer   = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.users.id}"
  }
}
resource "aws_apigatewayv2_integration" "api" {
  count                  = var.deploy_api ? 1 : 0
  api_id                 = aws_apigatewayv2_api.app[0].id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.zip["api"].invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 29000
}
resource "aws_apigatewayv2_route" "api" {
  count              = var.deploy_api ? 1 : 0
  api_id             = aws_apigatewayv2_api.app[0].id
  route_key          = "ANY /api/v1/{proxy+}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito[0].id
  target             = "integrations/${aws_apigatewayv2_integration.api[0].id}"
}
resource "aws_apigatewayv2_stage" "app" {
  count       = var.deploy_api ? 1 : 0
  api_id      = aws_apigatewayv2_api.app[0].id
  name        = "$default"
  auto_deploy = true
  default_route_settings {
    throttling_rate_limit  = 5
    throttling_burst_limit = 10
  }
}
resource "aws_lambda_permission" "api" {
  count         = var.deploy_api ? 1 : 0
  statement_id  = "OnlyThisApiGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.zip["api"].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.app[0].execution_arn}/*/*/api/v1/*"
}
resource "aws_lambda_event_source_mapping" "zip_queues" {
  for_each                = var.deploy_api ? toset(["delete", "notify", "infer"]) : toset([])
  event_source_arn        = aws_sqs_queue.work[each.key].arn
  function_name           = aws_lambda_function.zip[each.key].arn
  batch_size              = 1
  enabled                 = each.key != "infer" || local.processing_ready
  function_response_types = ["ReportBatchItemFailures"]
  scaling_config { maximum_concurrency = 2 }
}
resource "aws_lambda_event_source_mapping" "ingest" {
  count                   = local.has_worker ? 1 : 0
  event_source_arn        = aws_sqs_queue.work["ingest"].arn
  function_name           = aws_lambda_function.ingest[0].arn
  batch_size              = 1
  enabled                 = local.processing_ready
  function_response_types = ["ReportBatchItemFailures"]
  scaling_config { maximum_concurrency = 2 }
}
resource "aws_s3_bucket_notification" "uploads" {
  count  = local.processing_ready ? 1 : 0
  bucket = local.bucket_name
  queue {
    queue_arn     = aws_sqs_queue.work["ingest"].arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = "incoming/"
  }
  depends_on = [aws_sqs_queue_policy.s3_ingest, aws_lambda_event_source_mapping.ingest]
}
resource "aws_cloudwatch_event_rule" "maintenance" {
  for_each            = var.deploy_api ? { outbox = "rate(1 minute)", cleanup = "rate(5 minutes)" } : {}
  name                = "${local.name}-${each.key}"
  schedule_expression = each.value
}
resource "aws_cloudwatch_event_target" "maintenance" {
  for_each = aws_cloudwatch_event_rule.maintenance
  rule     = each.value.name
  arn      = aws_lambda_function.zip[each.key].arn
}
resource "aws_lambda_permission" "maintenance" {
  for_each      = aws_cloudwatch_event_rule.maintenance
  statement_id  = "OnlyOwnMaintenanceRule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.zip[each.key].function_name
  principal     = "events.amazonaws.com"
  source_arn    = each.value.arn
}
output "api_url" { value = var.deploy_api ? aws_apigatewayv2_api.app[0].api_endpoint : "" }
output "processing_enabled" { value = local.processing_ready }
output "lambda_names" { value = { for k, f in aws_lambda_function.zip : k => f.function_name } }
