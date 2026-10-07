locals {
  name          = "${var.prefix}-${var.environment}"
  bucket_name   = "${local.name}-${data.aws_caller_identity.current.account_id}-media"
  bucket_arn    = "arn:aws:s3:::${local.bucket_name}"
  queue_timeout = { ingest = 720, infer = 2880, delete = 720, notify = 360 }
  tables = {
    media         = { hash = "mediaId", range = null }
    operations    = { hash = "operationId", range = null }
    search        = { hash = "scopeSpecies", range = "observedAtMediaId" }
    subscriptions = { hash = "ownerSub", range = "subscriptionId" }
    outbox        = { hash = "eventId", range = null }
  }
}
# Learner Lab explicitly denies GetBucketObjectLockConfiguration, which the
# aws_s3_bucket resource reads after creation. scripts/bootstrap-aws-bucket.ps1
# creates/verifies the bucket using only CreateBucket and HeadBucket. This module
# manages its privacy, TLS, encryption, CORS and event configuration.
resource "aws_s3_bucket_public_access_block" "media" {
  bucket                  = local.bucket_name
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_server_side_encryption_configuration" "media" {
  bucket = local.bucket_name
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
resource "aws_s3_bucket_cors_configuration" "media" {
  bucket = local.bucket_name
  cors_rule {
    allowed_headers = ["content-type", "x-amz-checksum-sha256", "x-amz-meta-*"]
    allowed_methods = ["PUT", "GET", "HEAD"]
    allowed_origins = var.frontend_origins
    expose_headers  = ["ETag", "x-amz-checksum-sha256", "x-amz-version-id"]
    max_age_seconds = 300
  }
}
resource "aws_s3_bucket_policy" "media_tls" {
  bucket = local.bucket_name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport", Effect = "Deny", Principal = "*", Action = "s3:*"
      Resource  = [local.bucket_arn, "${local.bucket_arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}
resource "aws_dynamodb_table" "data" {
  for_each     = local.tables
  name         = "${local.name}-${each.key}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = each.value.hash
  range_key    = each.value.range
  attribute {
    name = each.value.hash
    type = "S"
  }
  dynamic "attribute" {
    for_each = each.value.range == null ? [] : [each.value.range]
    content {
      name = attribute.value
      type = "S"
    }
  }
  ttl {
    attribute_name = "expiresAt"
    enabled        = true
  }
  stream_enabled   = each.key == "outbox"
  stream_view_type = each.key == "outbox" ? "NEW_AND_OLD_IMAGES" : null
}
resource "aws_sqs_queue" "dead" {
  for_each                  = local.queue_timeout
  name                      = "${local.name}-${each.key}-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}
resource "aws_sqs_queue" "work" {
  for_each                   = local.queue_timeout
  name                       = "${local.name}-${each.key}"
  visibility_timeout_seconds = each.value
  message_retention_seconds  = 86400
  sqs_managed_sse_enabled    = true
  redrive_policy             = jsonencode({ deadLetterTargetArn = aws_sqs_queue.dead[each.key].arn, maxReceiveCount = 5 })
}
resource "aws_sqs_queue_policy" "s3_ingest" {
  queue_url = aws_sqs_queue.work["ingest"].url
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "AllowOwnedBucketEvents", Effect = "Allow", Principal = { Service = "s3.amazonaws.com" }
      Action = "sqs:SendMessage", Resource = aws_sqs_queue.work["ingest"].arn
      Condition = {
        ArnEquals    = { "aws:SourceArn" = local.bucket_arn }
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
      }
    }]
  })
}
resource "aws_cognito_user_pool" "users" {
  name                     = "${local.name}-users"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  username_configuration { case_sensitive = false }
  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }
  schema {
    name                = "email"
    attribute_data_type = "String"
    required            = true
    mutable             = true
    string_attribute_constraints {
      min_length = "3"
      max_length = "320"
    }
  }
  schema {
    name                = "given_name"
    attribute_data_type = "String"
    required            = true
    mutable             = true
    string_attribute_constraints {
      min_length = "1"
      max_length = "100"
    }
  }
  schema {
    name                = "family_name"
    attribute_data_type = "String"
    required            = true
    mutable             = true
    string_attribute_constraints {
      min_length = "1"
      max_length = "100"
    }
  }
  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
  verification_message_template { default_email_option = "CONFIRM_WITH_CODE" }
}
resource "aws_cognito_user_pool_client" "web" {
  name                          = "${local.name}-web"
  user_pool_id                  = aws_cognito_user_pool.users.id
  generate_secret               = false
  explicit_auth_flows           = ["ALLOW_USER_SRP_AUTH", "ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
  prevent_user_existence_errors = "ENABLED"
  access_token_validity         = 60
  id_token_validity             = 60
  refresh_token_validity        = 1
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
  read_attributes  = ["email", "email_verified", "given_name", "family_name"]
  write_attributes = ["email", "given_name", "family_name"]
}
resource "aws_ecr_repository" "worker" {
  name                 = "${local.name}-worker"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
}
